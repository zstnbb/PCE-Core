/**
 * PCE – Capture Queue (IndexedDB-backed offline buffer)
 *
 * When the PCE Core server is unreachable, captures are stored in IndexedDB
 * instead of being dropped.  A background retry loop flushes the queue once
 * the server comes back online.
 *
 * ES module — imported by service_worker.js:
 *   import { CaptureQueue } from "./capture_queue.js";
 */

// =========================================================================
// Config
// =========================================================================

const DB_NAME = "pce_capture_queue";
const DB_VERSION = 1;
const STORE_NAME = "pending_captures";
const TAG = "[PCE:queue]";

const BASE_RETRY_MS = 5000;
const MAX_RETRY_MS = 120000;
const MAX_QUEUE_SIZE = 2000;
const BATCH_SIZE = 20;
const MAX_AGE_MS = 7 * 24 * 3600 * 1000; // 7 days

let _db = null;
let _retryMs = BASE_RETRY_MS;
let _retryTimer = null;
let _flushing = false;

// =========================================================================
// IndexedDB lifecycle
// =========================================================================

function _openDB() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: "id",
          autoIncrement: true,
        });
        store.createIndex("created_at", "created_at", { unique: false });
      }
    };
    req.onsuccess = (e) => {
      _db = e.target.result;
      resolve(_db);
    };
    req.onerror = (e) => {
      console.error(TAG, "Failed to open IndexedDB:", e.target.error);
      reject(e.target.error);
    };
  });
}

// =========================================================================
// IDB helpers
// =========================================================================

function _promisify(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function _txComplete(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("Transaction aborted"));
  });
}

function _getAll(store, limit) {
  return new Promise((resolve) => {
    const items = [];
    const req = store.openCursor();
    req.onsuccess = (e) => {
      const cursor = e.target.result;
      if (cursor && items.length < limit) {
        items.push(cursor.value);
        cursor.continue();
      } else {
        resolve(items);
      }
    };
    req.onerror = () => resolve([]);
  });
}

// =========================================================================
// Core operations
// =========================================================================

async function enqueue(url, body) {
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
      await new Promise((resolve) => {
        cursorReq.onsuccess = (e) => {
          const cursor = e.target.result;
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
    console.error(TAG, "Enqueue failed:", err.message);
  }
}

async function flush() {
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
    const idsToDelete = [];
    const idsToUpdate = [];
    const now = Date.now();

    for (const item of items) {
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
          signal: AbortSignal.timeout(8000),
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
          const record = getReq.result;
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
    console.error(TAG, "Flush error:", err.message);
    _flushing = false;
    return { sent: 0, remaining: -1 };
  }
}

async function count() {
  try {
    const db = await _openDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    return await _promisify(tx.objectStore(STORE_NAME).count());
  } catch {
    return 0;
  }
}

async function clear() {
  try {
    const db = await _openDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).clear();
    await _txComplete(tx);
    console.log(TAG, "Queue cleared");
  } catch (err) {
    console.error(TAG, "Clear failed:", err.message);
  }
}

// =========================================================================
// Retry loop (exponential backoff)
// =========================================================================

function startRetryLoop() {
  if (_retryTimer) return;
  _scheduleNext();
  console.log(TAG, "Retry loop started");
}

function _scheduleNext() {
  _retryTimer = setTimeout(async () => {
    const pending = await count();
    if (pending > 0) {
      await flush();
    }
    _scheduleNext();
  }, _retryMs);
}

// =========================================================================
// Export
// =========================================================================

export const CaptureQueue = { enqueue, flush, count, clear, startRetryLoop };
