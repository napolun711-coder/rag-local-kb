
(function(){
  const button = document.getElementById('retry');
  const status = document.getElementById('status');
  const params = new URLSearchParams(location.search);
  const reason = params.get('reason');
  let checking = false;

  if (reason) {
    status.textContent = '暂时无法确认授权状态，请检查电脑端服务和局域网连接后重试。';
  }

  async function check(manual){
    if (checking) return;
    checking = true;
    button.disabled = true;
    if (manual) status.textContent = '正在重新检测授权状态…';
    let timer = 0;
    let controller = null;
    try {
      const options = {cache:'no-store'};
      if (typeof window.AbortController === 'function') {
        controller = new window.AbortController();
        options.signal = controller.signal;
        timer = window.setTimeout(function(){ controller.abort(); }, 4000);
      }
      const r = await fetch('/api/app/access/status', options);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (data && (!data.enabled || data.local || data.authorized)) {
        location.replace('/web');
        return;
      }
      status.textContent = '尚未检测到授权。请在电脑端确认“等待允许”中的设备请求。';
    } catch (err) {
      status.textContent = '暂时无法确认授权状态，请检查电脑端服务和局域网连接后重试。';
    } finally {
      if (timer) window.clearTimeout(timer);
      checking = false;
      button.disabled = false;
    }
  }
  button.addEventListener('click', function(){ check(true); });
  check(false);
  setInterval(function(){ check(false); }, 3000);
})();
