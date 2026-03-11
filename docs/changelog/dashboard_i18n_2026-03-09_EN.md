# Dashboard i18n Switching Instructions (2026-03-09)

This manual only records **already implemented and verified** frontend i18n changes, excluding speculative future plans.

---

## 1. One-sentence Conclusion

The frontend defaults to English, supporting one-click switching between English / Chinese in the top-right corner; the browser remembers your selection.

---

## 2. Changes Perceptible to the User

- When opening the Dashboard, the interface language is English by default
- A language toggle button has been added to the top-right corner
- Common static text switches accordingly after changing to Chinese
- Common date / number formats switch based on the current language
- Some common frontend error messages switch based on the current language

This is not a "hard-coded Chinese translation," but rather an integration into a standard i18n layer, providing a unified entry point for adding more languages in the future.

---

## 3. Current Interface Screenshots

The following images show the current frontend interface in English mode:

### Memory

<img src="../images/memory-palace-memory-page.png" width="900" alt="Memory Palace Memory Page (English mode)" />

### Review

<img src="../images/memory-palace-review-page.png" width="900" alt="Memory Palace Review Page (English mode)" />

### Maintenance

<img src="../images/memory-palace-maintenance-page.png" width="900" alt="Memory Palace Maintenance Page (English mode)" />

### Observability

<img src="../images/memory-palace-observability-page.png" width="900" alt="Memory Palace Observability Page (English mode)" />

---

## 4. What Was Actually Done

The corresponding frontend implementation is mainly in these files:

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

Current implementation scope:

- Default language: English
- Language switching: English / Chinese
- Persistence method: Browser `localStorage`
- Language switching no longer triggers redundant refreshes of protected data in Memory / Observability
- Agentation development toolbar: Not within the scope of this i18n update

---

## 5. Completed Verifications

This document only includes verification results that have been actually executed:

- Frontend `npm test`: `68 passed`
- Frontend `npm run build`: Passed
- Local API / SSE / frontend build integration check: Passed
- Docker `profile a / b / c / d` functional smoke tests: Passed
- `pwsh-in-docker` equivalent path for Windows: Re-run directly and passed

Note:

- "Passed" here means the functional paths and the frontend i18n changes themselves did not reveal any new blocking issues
- The previous false positive for `docs.skills_mcp_contract` has been corrected in the current `i18n` branch and is no longer a blocking item in these i18n conclusions

---

## 6. Usage Recommendations

- If you are just using it normally, simply use the default English interface
- If you prefer Chinese, click the language button in the top-right corner; no restart or configuration change is required
- If you see old screenshots in the documentation, refer to this manual and the `docs/images/*.png` files in the current repository
