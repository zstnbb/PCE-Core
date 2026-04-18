// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Capture Queue (IndexedDB-backed offline buffer).
 *
 * When the PCE Core server is unreachable, captures are stored in IndexedDB
 * instead of being dropped. A background retry loop flushes the queue once
 * the server comes back online.
 *
 * This is the TypeScript port of ``background/capture_queue.js``. Behaviour
 * is bit-identical; types are added so consumers get compile-time safety.
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const DB_NAME = "pce_capture_queue";
const DB_VERSION = 1;
const STORE_NAME = "pending_captures";
const TAG = "[PCE:queue]";

const BASE_RETRY_MS = 5_000;
const MAX_RETRY_MS = 120_000;
const MAX_QUEUE_SIZE = 2_000;
const BATCH_SIZE = 20;
const MAX_AGE_MS = 7 * 24 * 3_600 * 1_000; // 7 days

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface QueuedCapture {
  /** Auto-incremented primary key assigned by IndexedDB. */
  id?: number;
  /** Target ingest URL at enqueue time. */
  url: string;
  /** JSON string (request body). */
  body: string;
  /** `Date.now()` at enqueue. */
  created_at: number;
  /** How many retry attempts have been made so far. */
  attempts: number;
}

export interface FlushResult {
  sent: number;
  /** Number of items still queued, or ``-1`` on error. */
  remaining: number;
}

// ---------------------------------------------------------------------------
// Module-local state
// ---------------------------------------------------------------------------

let _db: IDBDatabase | null = null;
let _retryMs = BASE_RETRY_MS;
let _retryTimer: ReturnType<typeof setTimeout> | null = null;
let _flushing = false;

// ---------------------------------------------------------------------------
// IndexedDB lifecycle
// ---------------------------------------------------------------------------

function _openDB(): Promise<IDBDatabase> {
  if (_db) return Promise.resolve(_db);
  return new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: "id",
          autoIncrement: true,
        });
        store.createIndex("created_at", "created_at", { unique: false });
      }
    };
    req.onsuccess = (e) => {
      _db = (e.target as IDBOpenDBRequest).result;
      resolve(_db);
    };
    req.onerror = (e) => {
      const err = (e.target as IDBOpenDBRequest).error;
      console.error(TAG, "Failed to open IndexedDB:", err);
      reject(err ?? new Error("IndexedDB open failed"));
    };
  });
}

// ---------------------------------------------------------------------------
// IDB helpers
// ---------------------------------------------------------------------------

function _promisify<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function _txComplete(tx: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("Transaction aborted"));
  });
}

function _getAll(store: IDBObjectStore, limit: number): Promise<QueuedCapture[]> {
  return new Promise<QueuedCapture[]>((resolve) => {
    const items: QueuedCapture[] = [];
    const req = store.openCursor();
    req.onsuccess = (e) => {
      const cursor = (e.target as IDBRequest<IDBCursorWithValue | null>).result;
      if (cursor && items.length < limit) {
        items.push(cursor.value as QueuedCapture);
        cursor.continue();
      } else {
        resolve(items);
      }
    };
    req.onerror = () => resolve([]);
  });
}

// ---------------------------------------------------------------------------
// Core operations
// ---------------------------------------------------------------------------

async function enqueue(url: string, body: string | object): Promise<void> {
  try {
    const db = await _openDB();

    // Enforce max queue size — drop oldest if over limit
    const txCheck = db.transaction(STORE_NAME, "readwrite");
    const storeCheck = txCheck.objectStore(STORE_NAME);
    const currentCount = await _promisify(storeCheck.count());
    if (currentCount >= MAX_QUEUE_SIZE) {
      const toDelete = Math.max(1, Math.floor(MAX_QUEUE_SIZE * 0.1));
      let deleted = 0;
      const cursorReq = storeCheck.openCursor();
      await new Promise<void>((resolve) => {
        cursorReq.onsuccess = (e) => {
          const cursor = (e.target as IDBRequest<IDBCursorWithValue | null>).result;
          if (cursor && deleted < toDelete) {
            cursor.delete();
            deleted++;
            cursor.continue();
          } else {
            resolve();
          }
        };
        cursorReq.onerror = () => resolve();
      });
      console.warn(TAG, `Queue full (${MAX_QUEUE_SIZE}), dropped ${deleted} oldest`);
    }
    await _txComplete(txCheck);

    // Insert new item
    const txAdd = db.transaction(STORE_NAME, "readwrite");
    txAdd.objectStore(STORE_NAME).add({
      url,
      body: typeof body === "string" ? body : JSON.stringify(body),
      created_at: Date.now(),
      attempts: 0,
    });
    await _txComplete(txAdd);
    console.log(TAG, "Queued capture for retry");
  } catch (err) {
    console.error(TAG, "Enqueue failed:", (err as Error).message);
  }
}

async function flush(): Promise<FlushResult> {
  if (_flushing) return { sent: 0, remaining: 0 };
  _flushing = true;

  try {
    const db = await _openDB();
    const txRead = db.transaction(STORE_NAME, "readonly");
    const items = await _getAll(txRead.objectStore(STORE_NAME), BATCH_SIZE);

    if (items.length === 0) {
      _retryMs = BASE_RETRY_MS;
      _flushing = false;
      return { sent: 0, remaining: 0 };
    }

    let sent = 0;
    const idsToDelete: number[] = [];
    const idsToUpdate: Array<{ id: number; attempts: number }> = [];
    const now = Date.now();

    for (const item of items) {
      if (item.id === undefined) continue;

      // Discard items older than MAX_AGE_MS
      if (now - item.created_at > MAX_AGE_MS) {
        idsToDelete.push(item.id);
        continue;
      }

      try {
        const resp = await fetch(item.url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: item.body,
          signal: AbortSignal.timeout(8_000),
        });

        if (resp.ok) {
          idsToDelete.push(item.id);
          sent++;
        } else if (resp.status >= 400 && resp.status < 500) {
          idsToDelete.push(item.id);
          console.warn(TAG, `Discarding capture (HTTP ${resp.status})`);
        } else {
          idsToUpdate.push({ id: item.id, attempts: (item.attempts || 0) + 1 });
        }
      } catch {
        // Network error — server still offline, stop batch
        idsToUpdate.push({ id: item.id, attempts: (item.attempts || 0) + 1 });
        break;
      }
    }

    // Write back results
    if (idsToDelete.length > 0 || idsToUpdate.length > 0) {
      const txw = db.transaction(STORE_NAME, "readwrite");
      const storeW = txw.objectStore(STORE_NAME);
      for (const id of idsToDelete) {
        storeW.delete(id);
      }
      for (const { id, attempts } of idsToUpdate) {
        const getReq = storeW.get(id);
        getReq.onsuccess = () => {
          const record = getReq.result as QueuedCapture | undefined;
          if (record) {
            record.attempts = attempts;
            storeW.put(record);
          }
        };
      }
      await _txComplete(txw);
    }

    if (sent > 0) {
      _retryMs = BASE_RETRY_MS;
      console.log(TAG, `Flushed ${sent} queued capture(s)`);
    } else {
      _retryMs = Math.min(_retryMs * 2, MAX_RETRY_MS);
    }

    const remaining = await count();
    _flushing = false;
    return { sent, remaining };
  } catch (err) {
    console.error(TAG, "Flush error:", (err as Error).message);
    _flushing = false;
    return { sent: 0, remaining: -1 };
  }
}

async function count(): Promise<number> {
  try {
    const db = await _openDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    return await _promisify(tx.objectStore(STORE_NAME).count());
  } catch {
    return 0;
  }
}

async function clear(): Promise<void> {
  try {
    const db = await _openDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).clear();
    await _txComplete(tx);
    console.log(TAG, "Queue cleared");
  } catch (err) {
    console.error(TAG, "Clear failed:", (err as Error).message);
  }
}

// ---------------------------------------------------------------------------
// Retry loop (exponential backoff)
// ---------------------------------------------------------------------------

function startRetryLoop(): void {
  if (_retryTimer) return;
  _scheduleNext();
  console.log(TAG, "Retry loop started");
}

function _scheduleNext(): void {
  _retryTimer = setTimeout(async () => {
    const pending = await count();
    if (pending > 0) {
      await flush();
    }
    _scheduleNext();
  }, _retryMs);
}

// ---------------------------------------------------------------------------
// Public surface
// ---------------------------------------------------------------------------

export const CaptureQueue = {
  enqueue,
  flush,
  count,
  clear,
  startRetryLoop,
} as const;

export type CaptureQueueAPI = typeof CaptureQueue;

// Test hooks — keep at the bottom so shipping code ignores them unless
// deliberately imported.
export const __testing = {
  resetModuleState(): void {
    _db = null;
    _retryMs = BASE_RETRY_MS;
    if (_retryTimer) {
      clearTimeout(_retryTimer);
      _retryTimer = null;
    }
    _flushing = false;
  },
  constants: {
    BASE_RETRY_MS,
    MAX_RETRY_MS,
    MAX_QUEUE_SIZE,
    BATCH_SIZE,
    MAX_AGE_MS,
  },
};
