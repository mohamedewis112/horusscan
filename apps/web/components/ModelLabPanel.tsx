"use client";

import { useEffect, useState } from "react";

type ModelState = {
  key: string;
  role: string;
  modalities: string[];
  available: boolean;
  state?: string | null;
  inference_mode?: string | null;
  reason?: string | null;
};


export default function ModelLabPanel() {
  const [models, setModels] = useState<ModelState[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
    fetch(`${base}/v1/foundation/models`)
      .then((response) => response.json())
      .then((payload) => setModels(payload.models ?? []))
      .catch((modelError) =>
        setError(modelError instanceof Error ? modelError.message : "Could not load models.")
      );
  }, []);

  async function refreshModels() {
    setBusy(true);
    setError(null);
    try {
      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const response = await fetch(`${base}/v1/foundation/models`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "تعذر تحديث حالة الموديلات.");
      setModels(payload.models ?? []);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "تعذر تحديث حالة الموديلات.");
    } finally {
      setBusy(false);
    }
  }

  const stateLabel = (model: ModelState) => {
    if (model.available) return "يعمل الآن";
    if (model.state === "requires_gpu_runtime") return "يحتاج GPU";
    if (model.state === "runtime_and_data_required") return "يحتاج Runtime + بيانات";
    return "جاهز للتوصيل";
  };

  const stateClass = (model: ModelState) =>
    model.available ? "online" : model.state === "requires_gpu_runtime" ? "gpu" : "offline";

  return (
    <section className="model-lab" dir="rtl">
      <div className="validation-title">مختبر النماذج · Horus Council</div>
      <p className="muted">
        الأخضر يعني أن النموذج يشارك فعليًا في التحليل الحالي. الحالات الأخرى تعني أن الكود والـAdapter
        جاهزان لكن Runtime الحقيقي لم يُنشر بعد؛ لا توجد نتائج وهمية.
      </p>

      <div className="model-grid">
        {models.map((model) => (
          <div className="model-chip" key={model.key}>
            <span>{model.key}</span>
            <strong className={stateClass(model)}>{stateLabel(model)}</strong>
            <small>{model.role}</small>
            {!model.available && model.reason && <small>{model.reason}</small>}
          </div>
        ))}
      </div>

      <button className="tournament-button" disabled={busy} onClick={refreshModels}>
        {busy ? "جارٍ تحديث حالة الموديلات…" : "تحديث حالة الموديلات"}
      </button>

      {error && <div className="validation-error">{error}</div>}

      <div className="validation-note">
        وضع المعاينة لا يحتاج تسجيل دخول. مقارنة الأداء العلمية الكاملة تظل منفصلة عن تجربة الخريطة المباشرة.
      </div>
    </section>
  );
}
