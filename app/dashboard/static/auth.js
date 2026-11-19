/* Exchange an operator-provided bearer secret for a server-validated HttpOnly session cookie. */
(function () {
  "use strict";
  const rawFetch = window.fetch.bind(window);
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  window.__anzmaAuthReady = ready;
  window.__anzmaApiToken = null;

  function showLogin() {
    return new Promise(resolve => {
      const mount = () => {
        let overlay = document.getElementById("anzma-auth-overlay");
        if (!overlay) {
          overlay = document.createElement("div");
          overlay.id = "anzma-auth-overlay";
          overlay.innerHTML = '<form style="background:white;color:#17202a;padding:24px;border-radius:12px;max-width:360px;width:calc(100% - 40px);box-shadow:0 12px 48px #0005;font:14px system-ui"><h2 style="margin:0 0 8px">Sign in</h2><p>Enter the Core API bearer token provided by your operator.</p><label style="display:block">API token<input name="token" type="password" autocomplete="current-password" required style="display:block;width:100%;box-sizing:border-box;padding:10px;margin-top:6px"></label><p data-error role="alert" style="color:#a00;min-height:1.2em"></p><button style="padding:10px 16px">Continue</button></form>';
          Object.assign(overlay.style, {position:"fixed",inset:"0",zIndex:"99999",background:"#1119",display:"grid",placeItems:"center"});
          document.body.appendChild(overlay);
        }
        const form = overlay.querySelector("form");
        overlay.style.display = "grid";
        form.querySelector("[name=token]").focus();
        form.onsubmit = async event => {
          event.preventDefault();
          const token = form.querySelector("[name=token]").value.trim();
          try {
            const response = await rawFetch("/auth/session", {method:"POST", headers:{Authorization:`Bearer ${token}`}});
            if (response.status === 401) throw new Error("The token was rejected. Check it with your operator.");
            if (!response.ok) throw new Error(`Authentication failed (HTTP ${response.status}).`);
            form.querySelector("[name=token]").value = "";
            overlay.style.display = "none";
            resolve(true);
          } catch (error) {
            form.querySelector("[data-error]").textContent = error.message || "Could not verify the token.";
          }
        };
      };
      if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount, {once:true});
    });
  }

  (async () => {
    const response = await rawFetch("/dashboard/overview?hours=1");
    if (response.status !== 401) { resolveReady(true); return; }
    await showLogin();
    resolveReady(true);
  })();

  window.fetch = async function (input, init) {
    const requestUrl = typeof input === "string" ? input : (input instanceof Request ? input.url : input.toString());
    const url = new URL(requestUrl, window.location.href);
    const options = {...(init || {})};
    if (url.origin === window.location.origin) {
      await ready;
      options.credentials = options.credentials || "same-origin";
    }
    let response = await rawFetch(input, options);
    if (url.origin === window.location.origin && response.status === 401 &&
        !["/auth/session", "/auth/logout", "/health", "/ready"].includes(url.pathname) &&
        !url.pathname.startsWith("/api/v1/ingest/")) {
      await showLogin();
      response = await rawFetch(input, options);
    }
    return response;
  };
})();
