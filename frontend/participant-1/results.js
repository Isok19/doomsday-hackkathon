import { api, escape as e, answerLabel, evidenceButtons, bindEvidence, date } from '../shared/api.js';
import { openAppealForm } from '../participant-4/appeals.js';

export async function renderEvaluation(root, evaluation, dialog) {
  let mode = 'final';
  function render() {
    const result = evaluation[mode];
    root.innerHTML = `<div class="panel score-panel"><div><span class="eyebrow">${mode === 'final' ? 'ТЕКУЩИЙ РЕЗУЛЬТАТ' : 'ИСХОДНАЯ ОЦЕНКА'}</span><div class="score">${result.total === null ? '—' : result.total}<small>${result.total === null ? 'Нет применимых весовых критериев' : '/ 100'}</small></div><p>${e(evaluation.checklist.name)} · v${evaluation.checklist.version}</p></div><div class="score-icon">◎</div></div>
      <div class="segmented" aria-label="Версия результата"><button class="${mode === 'final' ? 'selected' : ''}" data-mode="final">Финальный результат</button><button class="${mode === 'original' ? 'selected' : ''}" data-mode="original">Исходная оценка</button></div>
      ${result.critical_errors.length ? '<div class="alert error">Выявлены грубые ошибки. Они отмечены отдельно от итогового балла.</div>' : evaluation.checklist.criteria.some(c => c.answer_type === 'flag') ? '<div class="alert success">Грубые ошибки не выявлены</div>' : ''}
      ${result.sections.length ? `<div class="panel section-scores">${result.sections.map(s => `<div><span>${e(s.name)}</span><strong>${s.points} / ${s.max_points}</strong></div>`).join('')}</div>` : ''}
      <div class="criteria-results">${result.results.map((r, index) => {
        const appeal = evaluation.appeals.find(a => a.criterion_id === r.criterion_id);
        return `<article class="panel result-card"><div class="result-title"><span class="criterion-number">${String(index + 1).padStart(2, '0')}</span><h3>${e(r.criterion.name)}</h3><span class="answer ${r.value === null ? 'neutral' : ''}">${answerLabel(r.value)}</span></div>
          <div class="result-meta">Вес: ${r.criterion.weight} · ${r.criterion.answer_type === 'flag' ? 'Отдельный флаг' : `Баллы: ${r.points === null ? 'N/A' : r.points} / ${r.effective_weight}`}</div><p>${e(r.explanation)}</p>
          ${r.na_reason ? `<p class="notice"><strong>Причина N/A:</strong> ${e(r.na_reason)}</p>` : ''}<div class="evidence-list">${evidenceButtons(r.evidence_message_ids, dialog)}</div>
          <div class="result-actions">${appeal ? `<a href="#appeal?id=${e(appeal.id)}">${appeal.status === 'resolved' ? 'Посмотреть решение' : 'Апелляция на рассмотрении'} →</a>` : `<button class="text-button" data-appeal="${e(r.criterion_id)}">Оспорить оценку</button>`}</div></article>`;
      }).join('')}</div>
      ${evaluation.appeals.some(a => a.decision) ? `<section class="panel panel-body"><h3>История решений</h3>${evaluation.appeals.filter(a => a.decision).map(a => `<div class="history-item"><strong>${e(a.original_answer.criterion.name)}</strong><small>${date(a.resolved_at)} · ${a.decision.action === 'confirm' ? 'Подтверждена' : 'Изменена'}</small><p>${e(a.decision.reason)}</p></div>`).join('')}</section>` : ''}`;
    root.querySelectorAll('[data-mode]').forEach(button => { button.onclick = () => { mode = button.dataset.mode; render(); }; });
    root.querySelectorAll('[data-appeal]').forEach(button => { button.onclick = () => openAppealForm(root, evaluation, button.dataset.appeal, async () => {
      evaluation = await api(`/evaluations/${evaluation.id}`); render();
    }); });
    bindEvidence(root);
  }
  render();
}
