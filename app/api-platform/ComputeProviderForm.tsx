"use client";

import { FormEvent, useRef, useState } from "react";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { FieldError, PageError, PageLoading } from "@/app/components/ui";
import type { ComputeCatalog, ComputePrivateImages, ComputeProvider } from "@/app/lib/compute";

const CUSTOM = "__custom__";

export function ComputeProviderForm({ provider, busy, onSubmit, onCancel }: {
  provider: ComputeProvider | null; busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>; onCancel: () => void;
}) {
  const catalog = useApiResource<ComputeCatalog>("/compute/manage/options");
  if (!catalog.data && catalog.loading) return <PageLoading />;
  if (catalog.error) return <PageError message={catalog.error} retry={catalog.reload} />;
  if (!catalog.data) return null;
  return <ProviderFields provider={provider} catalog={catalog.data} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />;
}

function ProviderFields({ provider, catalog, busy, onSubmit, onCancel }: {
  provider: ComputeProvider | null; catalog: ComputeCatalog; busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>; onCancel: () => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const [gpu, setGpu] = useState(provider ? catalog.gpu_specs.some(g => g.id === provider.gpu_spec_uuid) ? provider.gpu_spec_uuid : CUSTOM : "");
  const [customGpu, setCustomGpu] = useState(provider?.gpu_spec_uuid ?? "");
  const [gpuName, setGpuName] = useState(provider?.gpu_label ?? "");
  const [image, setImage] = useState(provider?.image_uuid ?? "");
  const [customImage, setCustomImage] = useState("");
  const [cuda, setCuda] = useState(provider?.cuda_v_from ?? 113);
  const [hasToken, setHasToken] = useState(false);
  const [privateImages, setPrivateImages] = useState<ComputePrivateImages | null>(null);
  const [imageLoading, setImageLoading] = useState(false), [imageError, setImageError] = useState("");
  const allCuda = [...new Set([111, 112, 113, 114, 116, 118, 121, 124, 128, cuda])].sort((a, b) => a - b);
  const selectedPublic = catalog.public_images.find(i => i.id === image);
  const selectedPrivate = privateImages?.images.some(i => i.id === image);
  const centers = [...catalog.data_centers, ...(provider?.data_centers ?? []).filter(id => !catalog.data_centers.some(c => c.id === id)).map(id => ({ id, name: `已配置地区：${id}` }))];

  function chooseGpu(value: string) {
    setGpu(value);
    const known = catalog.gpu_specs.find(g => g.id === value);
    if (known) setGpuName(known.name);
  }
  function chooseImage(value: string) {
    setImage(value);
    const known = catalog.public_images.find(i => i.id === value);
    if (known) setCuda(Math.max(111, known.cuda_v_from));
  }
  async function loadImages(more = false) {
    if (!formRef.current || imageLoading) return;
    const token = String(new FormData(formRef.current).get("token") ?? "").trim();
    setImageLoading(true); setImageError("");
    try {
      const result = await api<ComputePrivateImages>("/compute/manage/private-images", { method: "POST", cache: "no-store", ...jsonBody({ token, provider_id: provider?.id ?? null, page: more && privateImages ? privateImages.page + 1 : 1 }) });
      setPrivateImages(more && privateImages ? { ...result, images: Array.from(new Map([...privateImages.images, ...result.images].map(item => [item.id, item])).values()) } : result);
    } catch (error) { setImageError(formatApiError(error)); } finally { setImageLoading(false); }
  }
  return <form className="ai-form-panel" ref={formRef} onSubmit={event => { if (busy || imageLoading) { event.preventDefault(); return; } void onSubmit(event); }}>
    <div className="ai-section-title"><h3>{provider ? "编辑算力渠道" : "添加 AutoDL 渠道"}</h3><label className="ai-check"><input name="enabled" type="checkbox" defaultChecked={provider?.enabled ?? true} />启用渠道</label></div>
    <div className="ai-form-grid">
      <label className="form-field"><span>渠道名称</span><input name="name" required maxLength={80} defaultValue={provider?.name} placeholder="例如：比赛 GPU" /></label>
      <label className="form-field"><span>AutoDL 开发者 Token{provider ? " · 留空保留" : ""}</span><input name="token" type="password" required={!provider} autoComplete="new-password" disabled={imageLoading} placeholder="账户设置 → 开发者 Token" onChange={event => { setHasToken(Boolean(event.target.value.trim())); if (selectedPrivate && image !== provider?.image_uuid) setImage(""); setPrivateImages(null); setImageError(""); }} /></label>
      <label className="form-field"><span>GPU 型号</span><select required value={gpu} onChange={event => chooseGpu(event.target.value)}><option value="" disabled>请选择 GPU 型号</option>{catalog.gpu_specs.map(g => <option key={g.id} value={g.id}>{g.name} · {g.memory_gb} GB</option>)}<option value={CUSTOM}>自定义卡型</option></select></label>
      <label className="form-field"><span>每个实例 GPU 数量</span><select name="gpu_count" defaultValue={provider?.gpu_count ?? 1}>{[1, 2, 3, 4].map(count => <option key={count} value={count}>{count} 张 GPU</option>)}</select></label>
      <input type="hidden" name="gpu_spec_uuid" value={gpu === CUSTOM ? customGpu : gpu} /><input type="hidden" name="gpu_label" value={gpuName} />
      {gpu === CUSTOM && <><label className="form-field"><span>自定义卡型编号</span><input required value={customGpu} maxLength={80} onChange={event => setCustomGpu(event.target.value)} placeholder="填写 AutoDL 规格 ID" /></label><label className="form-field"><span>卡型显示名称</span><input required value={gpuName} maxLength={120} onChange={event => setGpuName(event.target.value)} /></label></>}
      <label className="form-field ai-wide"><span>运行镜像</span><select required value={image} onChange={event => chooseImage(event.target.value)}><option value="" disabled>请选择运行环境</option><optgroup label="公共基础镜像">{catalog.public_images.map(i => <option key={i.id} value={i.id}>{i.name} · CUDA {i.cuda_v_from / 10}</option>)}</optgroup>
        {!!privateImages?.images.length && <optgroup label="账号私有镜像">{privateImages.images.filter(i => !catalog.public_images.some(p => p.id === i.id)).map(i => <option key={i.id} value={i.id} disabled={!i.ready}>{i.name}{i.ready ? "" : "（尚未就绪）"}</option>)}</optgroup>}
        {image && image !== CUSTOM && !selectedPublic && !selectedPrivate && <option value={image}>当前配置的镜像</option>}
        <option value={CUSTOM}>自定义镜像</option></select><small>选择公共镜像后会自动匹配驱动版本下限。</small></label>
      <input type="hidden" name="image_uuid" value={image === CUSTOM ? customImage : image} />
      {image === CUSTOM && <label className="form-field ai-wide"><span>自定义镜像 UUID</span><input required maxLength={160} value={customImage} onChange={event => setCustomImage(event.target.value)} placeholder="填写 AutoDL 公共或私有镜像 UUID" /></label>}
      <div className="ai-wide"><div className="ai-button-row"><button className="outline-button" type="button" disabled={imageLoading || busy || (!hasToken && !provider?.has_token)} onClick={() => void loadImages()}>{imageLoading ? "正在读取…" : "读取账号私有镜像"}</button>{privateImages?.has_more && <button className="outline-button" type="button" disabled={imageLoading || busy} onClick={() => void loadImages(true)}>读取更多镜像</button>}</div><p className="ai-grant-scope">{privateImages ? privateImages.images.length ? `已读取 ${privateImages.images.length} 个私有镜像，可在上方选择。` : "账号没有私有镜像，可选择公共镜像或填写自定义镜像。" : "填写 Token 或使用已保存凭据后，可读取账号的私有镜像。读取不会保存凭据或创建实例。"}</p><FieldError>{imageError}</FieldError></div>
      <label className="form-field"><span>整实例预算单价 · 元 / 小时</span><input name="price" type="number" required min={0.001} step="0.001" defaultValue={provider ? provider.hourly_price_millis / 1000 : ""} placeholder="填写可接受的最高运行单价" /></label>
      <label className="form-field"><span>最低 CUDA 版本</span><select name="cuda_v_from" value={cuda} onChange={event => setCuda(Number(event.target.value))}>{allCuda.map(version => <option key={version} value={version}>CUDA {version / 10}</option>)}</select></label>
      <fieldset className="ai-channel-checks ai-wide"><legend>允许地区 · 不勾选则自动分配</legend>{centers.map(c => <label className="ai-check" key={c.id}><input name="centers" type="checkbox" value={c.id} defaultChecked={provider?.data_centers.includes(c.id)} />{c.name}</label>)}</fieldset>
    </div>
    <div className="ai-notice">卡型和公共镜像来自 AutoDL Pro 官方配置，库存由开机调度确认。首次开机会按量创建实例；实际价格超过预算单价时请求关机。已有队伍实例后不能更改镜像、卡型和卡数。</div>
    <div className="ai-button-row"><button className="primary-button" disabled={busy || imageLoading}>{busy ? "正在保存…" : "保存渠道"}</button><button type="button" className="outline-button" disabled={busy || imageLoading} onClick={onCancel}>取消</button></div>
  </form>;
}
