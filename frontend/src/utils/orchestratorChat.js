/**
 * Attach a live orchestrator plan to the chat timeline when history lacks
 * an embedded plan message (legacy sessions or pre-persistence plans).
 */
export function mergeActivePlanIntoMessages(messages, activePlan, activePlanId) {
  if (!activePlan || !activePlanId || !Array.isArray(messages)) {
    return messages;
  }

  const existingIdx = messages.findIndex((m) => m.orchestratorPlanId === activePlanId);
  if (existingIdx >= 0) {
    return messages.map((m, idx) =>
      idx === existingIdx ? { ...m, orchestratorPlan: activePlan } : m
    );
  }

  let insertIdx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const content = typeof messages[i].content === 'string' ? messages[i].content.trim() : '';
    if (messages[i].role === 'user' && (content.startsWith('/orchestrate ') || content.startsWith('/plan '))) {
      insertIdx = i + 1;
      break;
    }
  }

  const planMessage = {
    id: `asst_plan_${activePlanId}`,
    role: 'assistant',
    content: '',
    timestamp: new Date().toISOString(),
    orchestratorPlan: activePlan,
    orchestratorPlanId: activePlanId,
    messageType: 'orchestrator_plan',
    isLocal: false,
    status: 'persisted',
  };

  if (insertIdx >= 0) {
    const next = [...messages];
    next.splice(insertIdx, 0, planMessage);
    return next;
  }

  return [...messages, planMessage];
}

export function hydrateOrchestratorFields(msg) {
  return {
    ...msg,
    orchestratorPlan: msg.orchestratorPlan ?? msg.extra_data?.orchestratorPlan,
    orchestratorPlanId: msg.orchestratorPlanId ?? msg.extra_data?.orchestratorPlanId,
    messageType: msg.messageType ?? msg.extra_data?.messageType,
  };
}
