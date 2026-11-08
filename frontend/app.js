import { api, error } from './shared/api.js';
import { dialogsPage, dialogPage } from './participant-1/dialogs.js?v=0.3.0';
import { checklistsPage, checklistPage } from './participant-4/checklists.js';
import { appealsPage, appealPage } from './participant-4/appeals.js';

const routes = { dialogs: dialogsPage, dialog: dialogPage, checklists: checklistsPage, checklist: checklistPage, appeals: appealsPage, appeal: appealPage };
const labels = { dialogs: 'Диалоги', dialog: 'Карточка диалога', checklists: 'Чек-листы', checklist: 'Редактор чек-листа', appeals: 'Апелляции', appeal: 'Рассмотрение апелляции' };
async function route() {
  const [name = 'dialogs', search = ''] = location.hash.slice(1).split('?');
  const selected = routes[name] ? name : 'dialogs';
  document.querySelector('#breadcrumb').textContent = labels[selected];
  document.title = `${labels[selected]} · CQA`;
  document.querySelectorAll('[data-nav]').forEach(link => {
    const active = link.dataset.nav === (selected === 'dialog' ? 'dialogs' : selected === 'checklist' ? 'checklists' : selected === 'appeal' ? 'appeals' : selected);
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  const page = document.createElement('div');
  document.querySelector('#app').replaceChildren(page);
  page.innerHTML = '<div class="empty">Загрузка…</div>';
  try { await routes[selected](page, new URLSearchParams(search)); }
  catch (exception) { error(page, exception.message); }
}
window.addEventListener('hashchange', route);
route();
fetch('/health').then(response => {
  document.querySelector('#connection-status').textContent = response.ok ? 'Сервер подключён' : 'Сервер недоступен';
}).catch(() => { document.querySelector('#connection-status').textContent = 'Нет соединения'; });
