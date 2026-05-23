import { escapeHtml } from "../core/format.js";

export function renderMetrics(state) {
  const metrics = [
    ["任务", state.metrics.tasks],
    ["文件", state.metrics.files],
    ["工具调用", state.metrics.toolCalls],
    ["Token", state.metrics.tokens],
    ["验证", state.metrics.tests],
  ];

  return `
    <div class="metric-list">
      ${metrics
        .map(
          ([label, value]) => `
            <div class="metric-item">
              <span class="metric-label">${escapeHtml(label)}</span>
              <span class="metric-value">${escapeHtml(value)}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

export function renderBenchmarks(state) {
  return `
    <div class="benchmark-list">
      ${state.benchmarks.map(renderBenchmarkCard).join("")}
    </div>
  `;
}

function renderBenchmarkCard(item) {
  return `
    <article class="benchmark-card">
      <div class="benchmark-head">
        <span class="artifact-kind">${escapeHtml(item.category)}</span>
        <span class="badge ${escapeHtml(item.difficulty)}">${escapeHtml(item.difficulty)}</span>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.description)}</p>
      <div class="benchmark-checks">
        ${(item.acceptance_criteria || []).slice(0, 4).map((check) => `<span>${escapeHtml(check)}</span>`).join("")}
      </div>
      <button class="button secondary" data-action="run-benchmark" data-benchmark-id="${escapeHtml(item.id)}">运行基准</button>
    </article>
  `;
}

export function renderPreferences(state) {
  const profile = state.memoryProfile || {};
  const buckets = profile.buckets || [];
  return `
    <div class="preference-panel">
      <form class="preference-create" id="preference-create-form">
        <select id="preference-type">
          <option value="code_style">代码风格</option>
          <option value="ui_style">UI 风格</option>
          <option value="tech_stack">常用技术栈</option>
          <option value="testing">测试偏好</option>
          <option value="file_organization">文件组织</option>
        </select>
        <textarea id="preference-content" rows="2" placeholder="记录一个你希望 nanoCursor 记住的偏好"></textarea>
        <button class="button secondary" type="submit">保存偏好</button>
      </form>
      <section class="preference-summary">
        <div><strong>${escapeHtml(profile.preference_count ?? 0)}</strong><span>偏好记忆</span></div>
        <div><strong>${escapeHtml(profile.high_importance_count ?? 0)}</strong><span>高重要性</span></div>
        <div><strong>${escapeHtml(profile.total_memories ?? 0)}</strong><span>全部记忆</span></div>
      </section>
      ${profile.prompt_context ? `<pre class="preference-context">${escapeHtml(profile.prompt_context)}</pre>` : ""}
      <div class="preference-buckets">
        ${buckets.map(renderPreferenceBucket).join("")}
      </div>
    </div>
  `;
}

function renderPreferenceBucket(bucket) {
  const memories = bucket.memories || [];
  return `
    <article class="preference-bucket">
      <div class="preference-head">
        <div>
          <h3>${escapeHtml(bucket.label)}</h3>
          <p>${escapeHtml(bucket.description)}</p>
        </div>
        <span class="badge ${escapeHtml(bucket.confidence)}">${preferenceConfidenceLabel(bucket.confidence)}</span>
      </div>
      <div class="preference-memory-list">
        ${
          memories.length
            ? memories
                .map(
                  (memory) => `
                    <div class="preference-memory">
                      <span>${escapeHtml(memory.content)}</span>
                      <strong>${escapeHtml(memory.importance)}</strong>
                    </div>
                  `,
                )
                .join("")
            : `<div class="empty-mini">暂无偏好</div>`
        }
      </div>
    </article>
  `;
}

function preferenceConfidenceLabel(confidence) {
  const labels = {
    high: "高可信",
    medium: "已记录",
    empty: "待学习",
  };
  return labels[confidence] || confidence || "未知";
}

export function renderWorkspaceSettings(state) {
  const settings = state.workspaceSettings || {};
  const model = settings.model || {};
  const safety = settings.safety || {};
  const indexing = settings.indexing || {};
  return `
    <div class="settings-panel">
      <section class="settings-group">
        <h3>模型</h3>
        <div class="settings-field">
          <label>Provider</label>
          <input id="settings-model-provider" value="${escapeHtml(model.provider || "")}" placeholder="默认（自动检测）" />
        </div>
        <div class="settings-field">
          <label>Planner Model</label>
          <input id="settings-model-planner" value="${escapeHtml(model.planner_model || "")}" placeholder="继承 Provider" />
        </div>
        <div class="settings-field">
          <label>Coder Model</label>
          <input id="settings-model-coder" value="${escapeHtml(model.coder_model || "")}" placeholder="继承 Provider" />
        </div>
      </section>
      <section class="settings-group">
        <h3>安全</h3>
        <div class="settings-field checkbox">
          <input type="checkbox" id="settings-safety-shell" ${safety.require_approval_for_shell !== false ? "checked" : ""} />
          <label for="settings-safety-shell">Shell 执行需要审批</label>
        </div>
        <div class="settings-field checkbox">
          <input type="checkbox" id="settings-safety-delete" ${safety.require_approval_for_file_delete !== false ? "checked" : ""} />
          <label for="settings-safety-delete">删除文件需要审批</label>
        </div>
      </section>
      <section class="settings-group">
        <h3>索引</h3>
        <div class="settings-field">
          <label>忽略目录</label>
          <input id="settings-indexing-ignore" value="${escapeHtml((indexing.ignore || []).join(", "))}" placeholder="逗号分隔" />
        </div>
        <div class="settings-field">
          <label>最大文件大小 (KB)</label>
          <input id="settings-indexing-maxkb" value="${escapeHtml(indexing.max_file_size_kb || 512)}" type="number" />
        </div>
      </section>
      <button class="button primary compact-button" data-action="save-settings" type="button">保存设置</button>
    </div>
  `;
}
