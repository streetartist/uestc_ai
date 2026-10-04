"use client";

import type { SubmissionSchema } from "@/app/lib/domain";

export function SubmissionSchemaEditor({ value, onChange }: { value: SubmissionSchema; onChange: (value: SubmissionSchema) => void }) {
  const fields = value.fields ?? [];
  const definitions = value.field_definitions ?? {};
  const attachments = value.attachments ?? [];
  return <fieldset className="evaluation-editor">
    <legend>提交材料与表单</legend>
    <p className="evaluation-note">正式提交时检查必填材料，草稿可暂缺。字段标识使用英文小写、数字和下划线。</p>
    {fields.map((key, index) => <div className="manage-form" key={index}>
      <div className="form-grid">
        <label className="form-field"><span>字段标识</span><input value={key} onChange={(event) => {
          const next = event.target.value;
          const nextDefinitions = { ...definitions }; delete nextDefinitions[key]; nextDefinitions[next] = definitions[key] ?? {};
          onChange({ ...value, fields: fields.map((field, position) => position === index ? next : field), field_definitions: nextDefinitions });
        }} /></label>
        <label className="form-field"><span>显示名称</span><input value={definitions[key]?.label ?? key} onChange={(event) => onChange({ ...value, field_definitions: { ...definitions, [key]: { ...definitions[key], label: event.target.value } } })} /></label>
      </div>
      <div className="form-grid">
        <label className="form-field"><span>填写方式</span><select value={definitions[key]?.type ?? "text"} onChange={(event) => onChange({ ...value, field_definitions: { ...definitions, [key]: { ...definitions[key], type: event.target.value as "text" | "url" | "textarea" } } })}><option value="text">单行文字</option><option value="url">网址</option><option value="textarea">多行说明</option></select></label>
        <label className="form-field"><span>填写提示</span><input value={definitions[key]?.help ?? ""} onChange={(event) => onChange({ ...value, field_definitions: { ...definitions, [key]: { ...definitions[key], help: event.target.value } } })} /></label>
      </div>
      <label className="evaluation-toggle"><input type="checkbox" checked={definitions[key]?.required ?? false} onChange={(event) => onChange({ ...value, field_definitions: { ...definitions, [key]: { ...definitions[key], required: event.target.checked } } })} />正式提交必填</label>
      <button className="text-button" type="button" onClick={() => { const nextDefinitions = { ...definitions }; delete nextDefinitions[key]; onChange({ ...value, fields: fields.filter((_, position) => position !== index), field_definitions: nextDefinitions }); }}>移除此字段</button>
    </div>)}
    <button className="outline-button" type="button" onClick={() => {
      let number = fields.length + 1; while (fields.includes(`field_${number}`)) number++;
      const key = `field_${number}`;
      onChange({ ...value, fields: [...fields, key], field_definitions: { ...definitions, [key]: { label: "新材料说明", type: "text", required: false } } });
    }}>添加提交字段</button>
    <hr />
    {attachments.map((item, index) => <div className="manage-form" key={index}>
      <div className="form-grid">
        <label className="form-field"><span>附件名称</span><input value={item.label} onChange={(event) => onChange({ ...value, attachments: attachments.map((entry, position) => position === index ? { ...entry, label: event.target.value } : entry) })} /></label>
        <label className="form-field"><span>允许格式（英文逗号分隔）</span><input value={item.extensions.join(",")} onChange={(event) => onChange({ ...value, attachments: attachments.map((entry, position) => position === index ? { ...entry, extensions: event.target.value.toLowerCase().replace(/\./g, "").split(",").map((ext) => ext.trim()) } : entry) })} /></label>
      </div>
      <div className="form-grid">{([ ["min_count", "最少数量"], ["max_count", "最多数量"] ] as const).map(([key, label]) => <label className="form-field" key={key}><span>{label}</span><input type="number" min="0" max="20" value={item[key]} onChange={(event) => onChange({ ...value, attachments: attachments.map((entry, position) => position === index ? { ...entry, [key]: Number(event.target.value) } : entry) })} /></label>)}</div>
      <label className="form-field"><span>ZIP 内必须包含的文件（英文逗号分隔，可留空）</span><input value={item.required_files?.join(",") ?? ""} onChange={(event) => onChange({ ...value, attachments: attachments.map((entry, position) => position === index ? { ...entry, required_files: event.target.value.split(",").map((name) => name.trim()).filter(Boolean) } : entry) })} /></label>
      <button className="text-button" type="button" onClick={() => onChange({ ...value, attachments: attachments.filter((_, position) => position !== index) })}>移除此附件要求</button>
    </div>)}
    <button className="outline-button" type="button" onClick={() => {
      let number = attachments.length + 1; while (attachments.some((item) => item.key === `attachment_${number}`)) number++;
      onChange({ ...value, attachments: [...attachments, { key: `attachment_${number}`, label: "研究报告", extensions: ["pdf"], min_count: 1, max_count: 1 }] });
    }}>添加附件要求</button>
    <label className="evaluation-toggle"><input type="checkbox" checked={value.readme_required ?? false} onChange={(event) => onChange({ ...value, readme_required: event.target.checked })} />正式提交必须填写作品 README</label>
    <label className="form-field"><span>参赛者 README / 研究报告模板</span><textarea rows={10} value={value.readme_template ?? ""} onChange={(event) => onChange({ ...value, readme_template: event.target.value })} /><small>使用 Markdown；新作品和“恢复模板”会使用此内容。</small></label>
  </fieldset>;
}
