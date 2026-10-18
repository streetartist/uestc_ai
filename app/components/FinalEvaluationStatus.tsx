const messages = {
  pending: "作品已提交。组织方在截止后统一测评并发布结果。",
  queued: "最终评测已进入队列。",
  running: "最终评测正在运行。",
  failed: "最终评测出现异常，等待组织方核查。",
  awaiting_publication: "最终评测已完成，结果待统一发布。",
  completed: "最终评测结果已发布。",
} as const;

export function FinalEvaluationStatus({ status = "pending" }: { status?: keyof typeof messages }) {
  return <section className="evaluation-results" aria-label="最终评测状态">
    <header><strong>最终评测</strong></header><p role="status">{messages[status]}</p>
  </section>;
}
