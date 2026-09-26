/** 关于与社区弹窗 */
export const zhAbout = {
  title: '关于 R20',
  desc: '查看版本、许可与开源仓库',
  arch: {
    title: '系统架构',
    stack: 'FastAPI + Vue 3 纯静态 SPA',
    points: [
      '大模型全权裁决开平仓，Python 底座物理风控硬拦截',
      '100% 交易所云端 OCO 止损，Fail-Closed 设计',
      '多模型委员会辩论 + 启发式自进化记忆闭环',
    ],
  },
  repo: { title: '开源仓库', visit: '访问仓库', starHint: '欢迎 Star 与 Issue' },
  community: {
    title: '社区交流',
    qqGroup: '量化交流群',
    qqPersonal: '作者 QQ',
    linuxdo: 'LINUX DO 社区',
    // 2026-09：通道列表改为**后端出值**（`/api/v1/referral-channels`，公开只读），
    // 前端不再写死链接 ⇒ 这里只留"每种所叫什么"的展示文案。
    channel: '{venue} 专属通道',
    open: '打开注册页',
    copyHint: '点击复制',
  },
  version: '版本 {v} · 构建 {r}',
  license: 'MIT License · 开源仅供学习研究，不构成任何投资建议',
  risk: '风险提示：加密货币永续合约具有极高杠杆风险，历史收益不代表未来表现。',
};
