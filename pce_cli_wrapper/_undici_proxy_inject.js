// SPDX-License-Identifier: Apache-2.0
// pce_cli_wrapper undici proxy inject shim.
//
// Loaded via NODE_OPTIONS=--require=<this-file> from
// pce_cli_wrapper._proxy_env.augment_child_env when PCE_CLI_WRAPPER_PROXY
// is set. Replaces Node 22+ undici's global dispatcher with a
// ProxyAgent so that fetch() / undici-based HTTP clients (e.g. Google
// Gemini CLI) actually route through the operator's mitmproxy.
//
// Design notes:
//
// - We must NEVER throw. The shim is opt-in via NODE_OPTIONS, but if
//   it crashes the child process the user gets a confusing error
//   that has nothing to do with their actual command. Wrap everything
//   in try/catch and silently noop on any problem.
//
// - We only install if HTTPS_PROXY (or HTTP_PROXY) is set AND undici
//   can be required. If undici is missing (older Node), legacy
//   http.Agent behaviour already honours HTTPS_PROXY for non-fetch
//   clients, so we just do nothing here.
//
// - Diagnostic line is written to stderr only when
//   PCE_CLI_WRAPPER_PROXY_DEBUG=1 (operator opt-in).

(function injectUndiciProxy() {
  try {
    const proxy =
      process.env.HTTPS_PROXY ||
      process.env.https_proxy ||
      process.env.HTTP_PROXY ||
      process.env.http_proxy;

    if (!proxy) {
      return;
    }

    let undici;
    try {
      undici = require('undici');
    } catch (_e) {
      // undici not bundled (older Node); fall through silently.
      return;
    }

    const ProxyAgent = undici.ProxyAgent;
    const setGlobalDispatcher = undici.setGlobalDispatcher;
    if (typeof ProxyAgent !== 'function' || typeof setGlobalDispatcher !== 'function') {
      return;
    }

    setGlobalDispatcher(new ProxyAgent(proxy));

    if (process.env.PCE_CLI_WRAPPER_PROXY_DEBUG === '1') {
      try {
        process.stderr.write(
          '[pce-cli-wrapper] undici global dispatcher → ' + proxy + '\n'
        );
      } catch (_e) {
        // Ignore stderr write failure.
      }
    }
  } catch (_e) {
    // Total safety net — never break the child.
  }
})();
