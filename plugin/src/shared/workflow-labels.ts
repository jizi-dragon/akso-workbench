/** 工作流步骤类型的统一中文名；纯函数，host 与 client 都能安全引用。 */
export function workflowStepTypeLabel(stepType: number): string {
  switch (stepType) {
    case 10:
      return '开始'
    case 20:
      return '结束'
    case 30:
      return '任务'
    case 70:
      return '判断'
    case 90:
      return '动作'
    default:
      return `步骤 ${stepType}`
  }
}
