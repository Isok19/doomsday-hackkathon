import { api, query, escape as e, answerLabel, date, error, toast, busy, dialogLink, evidenceButtons, bindEvidence } from '../shared/api.js';
import { transcript } from '../participant-1/dialogs.js';

export function openAppealForm(root, evaluation, criterionId, onSaved) {
  const criterion = evaluation.checklist.criteria.find(c => c.id === criterionId);
  const modal = document.createElement('dialog');
  modal.innerHTML = `<div class="modal-heading"><h2>Оспорить оценку</h2><button class="icon-button" data-close aria-label="Закрыть">×</button></div><p>${e(criterion.name)}</p><form><label>Почему вы не согласны?<textarea name="comment" rows="5" required maxlength="5000"></textarea></label><div class="form-error"></div><button class="button primary">Подать апелляцию</button></form>`;
  root.append(modal); modal.showModal();
  modal.querySelector('[data-close]').onclick = () => modal.close();
  modal.addEventListener('close', () => modal.remove());
  modal.querySelector('form').onsubmit = async event => {
    event.preventDefault(); const form = event.currentTarget; const done = busy(form.querySelector('button'), 'Сохраняем…');
    try {
      await api('/appeals', { method: 'POST', body: { evaluation_id: evaluation.id, criterion_id: criterionId, comment: form.elements.comment.value } });
      modal.close(); toast('Апелляция сохранена'); await onSaved();
    } catch (exception) { error(form.querySelector('.form-error'), exception.message); } finally { done(); }
  };
}

export async function appealsPage(root) {
  const appeals = await api('/appeals');
  root.innerHTML = `<div class="page-heading"><div><div class="eyebrow">ПЕРЕСМОТР РЕЗУЛЬТАТОВ</div><h1>Апелляции</h1><p>Изучите доказательства и сохраните обоснованное решение.</p></div><span class="pill">Проверяющий</span></div>
    <div class="stats three"><article class="stat"><span>Все обращения</span><strong>${appeals.length}</strong></article><article class="stat"><span>На рассмотрении</span><strong>${appeals.filter(a => a.status === 'open').length}</strong></article><article class="stat"><span>Решения сохранены</span><strong>${appeals.filter(a => a.status === 'resolved').length}</strong></article></div>
    <section class="panel"><div class="panel-heading"><h2>Обращения</h2><label class="inline-label">Статус<select id="status"><option value="">Все</option><option value="open">На рассмотрении</option><option value="resolved">Рассмотрены</option></select></label></div><div id="appeal-list"></div></section>`;
  function render() {
    const status = root.querySelector('#status').value, filtered = appeals.filter(a => !status || a.status === status);
    root.querySelector('#appeal-list').innerHTML = filtered.length ? `<div class="table-scroll"><table><thead><tr><th>Диалог и критерий</th><th>Комментарий</th><th>Статус</th><th>Дата</th><th></th></tr></thead><tbody>${filtered.map(a => `<tr><td><a class="strong-link" href="#appeal?id=${e(a.id)}">${e(a.conversation_id)}</a><small class="block">${e(a.original_answer.criterion.name)}</small></td><td class="truncate">${e(a.comment)}</td><td><span class="pill ${a.status === 'open' ? 'amber' : ''}">${a.status === 'open' ? 'На рассмотрении' : 'Решение принято'}</span></td><td class="nowrap">${date(a.created_at)}</td><td><a class="row-link" href="#appeal?id=${e(a.id)}" aria-label="Открыть апелляцию">↗</a></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><span class="empty-symbol">⇄</span><h3>Апелляций пока нет</h3><p>Чтобы подать обращение, откройте результат оценки диалога и выберите спорный критерий.</p></div>';
  }
  root.querySelector('#status').onchange = render; render();
}

export async function appealPage(root, params) {
  const appeal = await api(`/appeals/${encodeURIComponent(params.get('id'))}`), evaluation = appeal.evaluation;
  const dialog = await api(`/dialogs/${encodeURIComponent(evaluation.conversation_id)}?${query({ dataset_group_id: evaluation.dataset_group_id })}`);
  const answer = appeal.original_answer, criterion = answer.criterion;
  const choices = criterion.answer_type === 'numeric' ? criterion.scale.map(s => [String(s.value), `${s.value} — ${s.description}`]) : [['true', 'Да'], ['false', 'Нет']];
  if (criterion.allow_na) choices.push(['null', 'N/A — неприменимо']);
  root.innerHTML = `<a class="back-link" href="#appeals">← Все апелляции</a><div class="page-heading"><div><div class="eyebrow">РАССМОТРЕНИЕ АПЕЛЛЯЦИИ</div><h1>${e(criterion.name)}</h1><p><a href="${dialogLink(evaluation.dataset_group_id, evaluation.conversation_id)}">${e(evaluation.conversation_id)} ↗</a> · ${date(appeal.created_at)}</p></div><span class="pill ${appeal.status === 'open' ? 'amber' : ''}">${appeal.status === 'open' ? 'На рассмотрении' : 'Решение принято'}</span></div>
    <div class="detail-grid"><section class="panel transcript-panel"><div class="panel-heading"><h2>Доказательства в контексте</h2></div>${transcript(dialog)}</section><div>
    <section class="panel panel-body"><div class="result-title"><h2>Спорная оценка</h2><span class="answer">${answerLabel(answer.value)}</span></div><p>${e(answer.explanation)}</p>${answer.na_reason ? `<p class="notice">N/A: ${e(answer.na_reason)}</p>` : ''}<div class="evidence-list">${evidenceButtons(answer.evidence_message_ids, dialog)}</div><div class="divider"></div><h3>Комментарий к апелляции</h3><p class="quoted">${e(appeal.comment)}</p><small class="muted">Правила: ${e(evaluation.checklist.name)} · версия ${evaluation.checklist.version}</small></section>
    <section class="panel panel-body decision-panel">${appeal.decision ? `<h2>${appeal.decision.action === 'confirm' ? 'Оценка подтверждена' : 'Оценка изменена'}</h2><p>${e(appeal.decision.reason)}</p><small>${date(appeal.resolved_at)}</small><div class="comparison"><div><small>Исходный итог</small><strong>${evaluation.original.total ?? '—'}</strong></div><span>→</span><div><small>Финальный итог</small><strong>${evaluation.final.total ?? '—'}</strong></div></div><a class="button" href="${dialogLink(evaluation.dataset_group_id, evaluation.conversation_id)}&evaluation=${evaluation.id}">Открыть результат →</a>` : `
      <h2>Решение проверяющего</h2><form id="decision-form"><label>Действие<select name="action"><option value="confirm">Подтвердить оценку</option><option value="change">Изменить оценку</option></select></label>
      <fieldset id="replacement" hidden disabled><label>Новая оценка<select name="value">${choices.map(([value, label]) => `<option value="${value}" ${String(answer.value) === value ? 'selected' : ''}>${e(label)}</option>`).join('')}</select></label><label>Обоснование новой оценки<textarea name="explanation" rows="3" required maxlength="10000"></textarea></label><label id="na-reason-label" hidden>Причина неприменимости<textarea name="na_reason" maxlength="3000"></textarea></label><label>Подтверждающие реплики<select name="evidence" multiple size="6" required>${dialog.transcript.map(m => `<option value="${e(m.message_id)}" ${answer.evidence_message_ids.includes(m.message_id) ? 'selected' : ''}>#${m.position} · ${e(m.timestamp)} · ${e(m.text.slice(0, 90))}</option>`).join('')}</select><small>Для выбора нескольких реплик удерживайте ⌘ на Mac или Ctrl на Windows.</small></label></fieldset>
      <label>Причина решения<textarea name="reason" required rows="3" maxlength="5000"></textarea></label><div class="form-error"></div><button class="button primary full">Сохранить решение</button></form>`}</section>
    </div></div>`;
  bindEvidence(root);
  const form = root.querySelector('#decision-form');
  if (!form) return;
  function updateNa() {
    const na = form.elements.value.value === 'null';
    form.querySelector('#na-reason-label').hidden = !na; form.elements.na_reason.required = na;
  }
  form.elements.action.onchange = () => {
    const change = form.elements.action.value === 'change';
    const fieldset = form.querySelector('#replacement'); fieldset.hidden = !change; fieldset.disabled = !change; updateNa();
  };
  form.elements.value.onchange = updateNa;
  form.onsubmit = async event => {
    event.preventDefault(); const done = busy(form.querySelector('[type="submit"]') || form.querySelector('button'), 'Сохраняем…');
    try {
      const body = { action: form.elements.action.value, reason: form.elements.reason.value };
      if (body.action === 'change') body.replacement = { criterion_id: criterion.id, value: JSON.parse(form.elements.value.value), explanation: form.elements.explanation.value,
        evidence_message_ids: [...form.elements.evidence.selectedOptions].map(option => option.value), na_reason: form.elements.value.value === 'null' ? form.elements.na_reason.value : '' };
      await api(`/appeals/${appeal.id}/decision`, { method: 'POST', body });
      toast('Решение сохранено, итог пересчитан'); await appealPage(root, params);
    } catch (exception) { error(form.querySelector('.form-error'), exception.message); } finally { done(); }
  };
}
