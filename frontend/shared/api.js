export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      ...options, headers: { 'Content-Type': 'application/json', ...options.headers },
      body: options.body === undefined ? undefined : typeof options.body === 'string' ? options.body : JSON.stringify(options.body),
    });
  } catch {
    throw new Error('Нет соединения с сервером. Проверьте, запущен ли он в терминале.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map(item => `${item.loc?.slice(1).join('.')}: ${item.msg}`).join('; ')
      : typeof detail === 'string' ? detail : `Ошибка сервера (${response.status})`;
    throw new Error(message);
  }
  return data;
}

export const query = values => new URLSearchParams(Object.entries(values).filter(([, v]) => v !== '' && v != null)).toString();
export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
export const date = value => new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
export const answerLabel = value => value === null ? 'N/A' : value === true ? 'Да' : value === false ? 'Нет' : String(value);
export function toast(message) {
  const node = document.querySelector('#toast');
  node.textContent = message; node.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { node.hidden = true; }, 5500);
}
export function error(container, message) { container.innerHTML = `<div class="alert error" role="alert">${escape(message)}</div>`; }
export function busy(button, label) {
  const text = button.textContent; button.disabled = true; button.textContent = label;
  return () => { button.disabled = false; button.textContent = text; };
}
export function safeUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
  catch { return ''; }
}
export const dialogLink = (group, id) => `#dialog?${query({ group, id })}`;
export function evidenceButtons(ids, dialog) {
  return ids.map(id => { const message = dialog.transcript.find(m => m.message_id === id);
    return message ? `<button type="button" class="evidence" data-evidence="${escape(id)}">↗ ${escape(message.timestamp)} · реплика ${message.position}</button>` : '';
  }).join('');
}
export function bindEvidence(container) {
  container.querySelectorAll('[data-evidence]').forEach(button => button.addEventListener('click', () => {
    const target = document.getElementById(`message-${button.dataset.evidence}`);
    if (target) {
      document.querySelectorAll('.message.highlight').forEach(node => node.classList.remove('highlight'));
      target.classList.add('highlight'); target.scrollIntoView({ behavior: 'smooth', block: 'center' }); target.focus({ preventScroll: true });
    }
  }));
}
