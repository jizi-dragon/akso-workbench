"""egmp.writers —— akso-auto 写路径 Python 化（阶段 3B，已完成核心）。

映射（源 akso-auto/scripts → 目标）：
    create-object.js                     → objects.py
    create-field.js + field-router.js    → fields.py
    create-picklist.js                   → picklists.py
    create-lifecycle-status.js 等 4 文件 → lifecycle.py
    create-workflow.js + workflow-builder.js + create-workflow-step.js
                                         → workflows.py
    create-menu.js                       → menus.py
    save-form-layout.js + form-layout-builder.js
    save-list-layout.js + list-layout-builder.js → layouts.py
    validate-blueprint.js + spec-generator.js → blueprint.py
    idempotency.js                       → idempotency.py
    api/*.js 端点与枚举常量              → endpoints.py（只读提取，实证对齐）

编排闭环在 ../orchestrate.py（runFullWorkflow / topologicalSort / checkpoint）。
"""

__all__ = ["blueprint", "endpoints", "fields", "idempotency", "layouts", "lifecycle",
           "menus", "objects", "picklists", "workflows"]
