# Dashboard 中英切换说明（2026-03-09）

这份说明只记录**已经落地、已经验证**的前端 i18n 变化，不写猜测中的后续计划。

---

## 1. 一句话结论

当前前端默认英文，右上角支持一键切换英文 / 中文；浏览器会记住你的选择。

---

## 2. 用户能直接感知到的变化

- 默认打开 Dashboard 时，界面语言是英文
- 右上角新增语言切换按钮
- 切到中文后，常见静态文案会跟着切换
- 常见日期 / 数字格式会跟着当前语言切换
- 前端侧的一部分常见错误提示也会跟着当前语言切换

这次不是“把英文硬改成中文”，而是接入了标准 i18n 层，后续继续加别的语言也有统一入口。

---

## 3. 当前界面截图

下面这组图展示的是切到中文后的当前前端界面：

### Memory

<img src="../images/memory-zh.png" width="900" alt="Memory Palace Memory 页面（中文模式）" />

### Review

<img src="../images/review-zh.png" width="900" alt="Memory Palace Review 页面（中文模式）" />

### Maintenance

<img src="../images/maintenance-zh.png" width="900" alt="Memory Palace Maintenance 页面（中文模式）" />

### Observability

<img src="../images/observability-zh.png" width="900" alt="Memory Palace Observability 页面（中文模式）" />

---

## 4. 这次实际做了什么

对应前端实现主要在这些文件：

- `frontend/src/i18n.js`
- `frontend/src/locales/en.js`
- `frontend/src/locales/zh-CN.js`
- `frontend/src/lib/format.js`
- `frontend/src/App.jsx`
- `frontend/src/features/memory/MemoryBrowser.jsx`
- `frontend/src/features/review/ReviewPage.jsx`
- `frontend/src/features/maintenance/MaintenancePage.jsx`
- `frontend/src/features/observability/ObservabilityPage.jsx`
- `frontend/src/components/SnapshotList.jsx`
- `frontend/src/components/DiffViewer.jsx`
- `frontend/src/lib/api.js`

当前实现口径：

- 默认语言：英文
- 语言切换：英文 / 中文
- 持久化方式：浏览器 `localStorage`
- 语言切换不会再触发 Memory / Observability 受保护数据的重复刷新
- Agentation 开发态工具条：不在这次 i18n 范围内

---

## 5. 已完成的验证

这次文档里只保留已经实际跑过的验证结论：

- 前端 `npm test`：`68 passed`
- 前端 `npm run build`：通过
- 本地 API / SSE / frontend build 联调检查：通过
- Docker `profile a / b / c / d` 功能性 smoke：通过
- `pwsh-in-docker` 的 Windows 等效链路：直接重跑通过

说明：

- 这里说的“通过”，是指功能链路与前端 i18n 改动本身没有发现新的阻断问题
- 历史上那个 `docs.skills_mcp_contract` 的误报已经在当前 `i18n` 分支修正，不再是这次 i18n 结论里的阻断项

---

## 6. 使用建议

- 如果你只是日常使用，直接按默认英文界面使用即可
- 如果你更习惯中文，点右上角语言按钮即可，不需要重启或改配置
- 如果你在文档里看到旧截图，以这份说明和当前仓库里的 `docs/images/*.png` 为准
