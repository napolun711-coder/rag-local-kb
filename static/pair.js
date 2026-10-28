
(function(){
  const title = document.getElementById('title');
  const mark = document.getElementById('mark');
  const message = document.getElementById('message');
  const retry = document.getElementById('retryBtn');
  let request = null;
  let converting = false;
  let stopped = false;
  let retryTimer = null;

  function platformName() {
    const ua = navigator.userAgent || '';
    if (/iPad/i.test(ua)) return 'iPad';
    if (/iPhone/i.test(ua)) return 'iPhone';
    if (/Android/i.test(ua)) return 'Android 平板';
    if (/Windows/i.test(ua)) return 'Windows 设备';
    if (/Macintosh|Mac OS/i.test(ua)) return 'Mac 设备';
    return '局域网设备';
  }

  function deviceName() {
    let suffix = '';
    try {
      suffix = String(sessionStorage.getItem('ragkb_pair_suffix') || '');
    } catch (err) {}
    if (!suffix) {
      const bytes = new Uint8Array(2);
      if (window.crypto && crypto.getRandomValues) {
        crypto.getRandomValues(bytes);
      } else {
        bytes[0] = Math.floor(Math.random() * 255);
        bytes[1] = Math.floor(Math.random() * 255);
      }
      suffix = ((bytes[0] << 8) | bytes[1]).toString(36).toUpperCase().padStart(3, '0').slice(-3);
      try { sessionStorage.setItem('ragkb_pair_suffix', suffix); } catch (err) {}
    }
    return platformName() + ' · ' + suffix;
  }

  function setMessage(text, kind) {
    message.className = 'note' + (kind ? ' ' + kind : '');
    message.textContent = text;
  }

  function showRetry(text) {
    retry.hidden = false;
    if (text) { setMessage(text, 'error'); }
  }

  function saveRequest(data) {
    request = {
      id: data.request_id,
      secret: data.request_secret,
      name: data.name,
      expiresAt: Date.now() + Math.max(60, Number(data.expires_in || 300)) * 1000
    };
    try { sessionStorage.setItem('ragkb_pair_request', JSON.stringify(request)); } catch (err) {}
  }

  function clearRequest() {
    request = null;
    try { sessionStorage.removeItem('ragkb_pair_request'); } catch (err) {}
  }

  function restoreRequest() {
    try {
      const saved = JSON.parse(sessionStorage.getItem('ragkb_pair_request') || 'null');
      if (saved && saved.id && saved.secret && Number(saved.expiresAt) > Date.now()) {
        request = saved;
      }
    } catch (err) {}
  }

  async function authorizedElsewhere() {
    try {
      const response = await fetch('/api/app/access/status', {cache: 'no-store'});
      const data = await response.json();
      if (data && (!data.enabled || data.local || data.authorized)) {
        location.replace('/web');
        return true;
      }
    } catch (err) {}
    return false;
  }

  async function createRequest() {
    clearTimeout(retryTimer);
    retry.hidden = true;
    setMessage('正在发送连接请求…');
    try {
      const response = await fetch('/api/app/access/requests', {
        method: 'POST',
        cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_name: deviceName()})
      });
      const data = await response.json().catch(function(){ return {}; });
      if (!response.ok) {
        const detail = data && data.detail;
        throw new Error((detail && (detail.message || detail)) || ('HTTP ' + response.status));
      }
      saveRequest(data);
      setMessage('已发送：' + request.name + '。请到电脑设置页点“允许”。');
      poll();
    } catch (err) {
      showRetry('发送失败：' + ((err && err.message) || '网络不可用'));
    }
  }

  async function poll() {
    if (stopped || converting || !request) return;
    if (Date.now() >= Number(request.expiresAt || 0)) {
      clearRequest();
      setMessage('上次请求已过期，正在自动刷新…');
      retryTimer = setTimeout(createRequest, 600);
      return;
    }
    converting = true;
    try {
      const response = await fetch(
        '/api/app/access/requests/' + encodeURIComponent(request.id) + '/claim',
        {
          method: 'POST',
          cache: 'no-store',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({request_secret: request.secret})
        }
      );
      const data = await response.json().catch(function(){ return {}; });
      if (response.status === 410) {
        clearRequest();
        setMessage('连接请求已刷新，请查看电脑设置页…');
        retryTimer = setTimeout(createRequest, 700);
        return;
      }
      if (!response.ok) {
        const detail = data && data.detail;
        throw new Error((detail && (detail.message || detail)) || ('HTTP ' + response.status));
      }
      if (data.status === 'approved') {
        stopped = true;
        mark.classList.remove('waiting');
        mark.textContent = '✓';
        title.textContent = '连接成功';
        setMessage('连接成功，正在打开聊天页面…', 'ok');
        retry.hidden = true;
        setTimeout(function(){ location.replace(data.redirect || '/web'); }, 1100);
        return;
      }
      setMessage('已发送：' + request.name + '。请在电脑设置页点“允许”。');
    } catch (err) {
      setMessage('网络短暂中断，正在自动重试…');
    } finally {
      converting = false;
    }
    if (!stopped) { retryTimer = setTimeout(poll, 1200); }
  }

  retry.addEventListener('click', createRequest);
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden && request && !stopped) { setTimeout(poll, 150); }
  });

  (async function start() {
    if (await authorizedElsewhere()) return;
    restoreRequest();
    if (request) {
      setMessage('已发送：' + request.name + '。请在电脑设置页点“允许”。');
      poll();
    } else {
      createRequest();
    }
  })();
})();
