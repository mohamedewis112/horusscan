"use client";

import { useCallback, useEffect, useState } from "react";

type Service = { status: string; label: string; detail: string };
type Health = { checked_at: string; model: Service; database: Service; executor: Service; test_scope: string };

export default function ConnectivityPanel() {
  const [data, setData] = useState<Health | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setChecking(true);
    setError("");
    try {
      const response = await fetch("/api/lab/status", { cache: "no-store", signal });
      if (!response.ok) throw new Error("تعذر قراءة حالة الخدمات من الخادم.");
      setData(await response.json() as Health);
    } catch (e) {
      if (signal?.aborted) return;
      setError(e instanceof Error ? e.message : "حدث خطأ في الاتصال.");
      setData(null);
    } finally {
      if (!signal?.aborted) setChecking(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const entries: { id: string; title: string; info: Service | undefined }[] = [
    { id: "model", title: "GROQ · AI MODEL", info: data?.model },
    { id: "executor", title: "ARTEX · EXECUTOR", info: data?.executor },
    { id: "database", title: "SUPABASE · DATABASE", info: data?.database },
  ];
  return (
    <section aria-label="حالة الاتصال الحقيقية" className="connection-section">
      <div className="connection-heading">
        <div><strong>حالة الخدمات الفعلية</strong><small>بيانات مباشرة من خادم عين حورس، وليست مؤشرات تجريبية</small></div>
        <button onClick={() => void refresh()} disabled={checking} type="button">{checking ? "جارٍ الفحص..." : "إعادة فحص الاتصال"}</button>
      </div>
      {error && <p className="connection-error" role="alert">{error}</p>}
      <div className="metrics">
        {entries.map(item => {
          const good = item.info?.status === "ready";
          return <div className="metric" key={item.id}>
            <small>{item.title}</small>
            <strong className={good ? "connection-ok" : "connection-warn"}>{checking && !item.info ? "جارٍ التحقق" : item.info?.label || "الحالة غير متاحة"}</strong>
            <span>{item.info?.detail || "قراءة حالة الخدمة من API..."}</span>
          </div>;
        })}
      </div>
      {data && <p className="connection-footnote">آخر تحقق: {new Date(data.checked_at).toLocaleString("ar-EG")} · لا ينفّذ الفحص أي اتصال بموقع خارجي سوى Groq للتحقق من النموذج.</p>}
    </section>
  );
}
