"use client";

import { useMemo, useState } from "react";

type Severity = "متوسط" | "منخفض" | "معلوماتي";
type Finding = { id: string; severity: Severity; title: string; detail: string; recommendation: string };

function inspectHeaders(input: string): Finding[] {
  if (!input.trim()) return [];
  const values = new Map<string, string>();
  for (const line of input.split(/\r?\n/).slice(0, 100)) {
    const match = line.match(/^([a-z][a-z0-9-]{1,70})\s*:\s*(.*)$/i);
    if (!match) continue;
    const key = match[1].toLowerCase();
    if (!["set-cookie", "authorization", "cookie", "proxy-authorization"].includes(key)) {
      values.set(key, match[2].slice(0, 1200));
    }
  }
  if (!values.size) return [];
  const findings: Finding[] = [];
  const add = (id: string, severity: Severity, title: string, detail: string, recommendation: string) =>
    findings.push({ id, severity, title, detail, recommendation });
  const hsts = values.get("strict-transport-security") || "";
  if (!hsts) add("HSTS-MISSING", "متوسط", "غياب HSTS", "لم يظهر Strict-Transport-Security في الترويسات الملصقة.", "فعّل HSTS على نطاق HTTPS بعد التأكد من توافق النطاقات الفرعية.");
  else {
    const maxAge = Number(hsts.match(/max-age\s*=\s*(\d+)/i)?.[1] || 0);
    if (maxAge && maxAge < 15552000) add("HSTS-SHORT", "منخفض", "مدة HSTS قصيرة", "قيمة max-age أقل من 180 يومًا.", "قيّم رفع max-age تدريجيًا بعد التأكد من إعداد HTTPS.");
  }
  const csp = values.get("content-security-policy") || "";
  if (!csp) add("CSP-MISSING", "متوسط", "غياب سياسة أمن المحتوى", "لم تظهر Content-Security-Policy في العينة.", "اعتمد CSP مناسبة للموقع تدريجيًا مع وضع Report-Only للاختبار أولًا.");
  if ((values.get("x-content-type-options") || "").toLowerCase().trim() !== "nosniff") {
    add("NOSNIFF-MISSING", "متوسط", "عدم تفعيل nosniff", "ترويسة X-Content-Type-Options مفقودة أو ليست nosniff.", "أضف X-Content-Type-Options: nosniff.");
  }
  const xfo = (values.get("x-frame-options") || "").toUpperCase();
  if (!["DENY", "SAMEORIGIN"].includes(xfo) && !/frame-ancestors\s+[^;]+/i.test(csp)) {
    add("FRAME-POLICY", "متوسط", "لا توجد حماية ظاهرة من تضمين الصفحة", "لم تظهر سياسة frame-ancestors أو X-Frame-Options مناسبة.", "اضبط frame-ancestors في CSP بناءً على احتياجات التضمين.");
  }
  if (!values.has("referrer-policy")) {
    add("REFERRER-MISSING", "منخفض", "غياب Referrer-Policy", "لم تظهر سياسة صريحة لإحالة الروابط.", "استخدم strict-origin-when-cross-origin أو سياسة أضيق بحسب الحاجة.");
  }
  if (!values.has("permissions-policy")) {
    add("PERMISSIONS-MISSING", "معلوماتي", "غياب Permissions-Policy", "لم تُحدد قيود صريحة لواجهات المتصفح الحساسة.", "حدّد الميزات المسموحة للتطبيق مثل camera وmicrophone وgeolocation.");
  }
  return findings;
}

const DEMO = [
  "HTTP/2 200 OK",
  "content-type: text/html; charset=utf-8",
  "cache-control: private, no-store",
  "x-content-type-options: nosniff",
  "referrer-policy: strict-origin-when-cross-origin",
].join("\n");

export default function SecurityWorkbench() {
  const [raw, setRaw] = useState("");
  const [provider, setProvider] = useState<"groq" | "openrouter">("groq");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const findings = useMemo(() => inspectHeaders(raw), [raw]);
  const totals = useMemo(() => ({
    medium: findings.filter(x => x.severity === "متوسط").length,
    low: findings.filter(x => x.severity === "منخفض").length,
    info: findings.filter(x => x.severity === "معلوماتي").length
  }), [findings]);

  function downloadReport() {
    const report = {
      product: "Horus Security Lab",
      inspected_at: new Date().toISOString(),
      source: "manually pasted HTTP response headers",
      limitations: "Local heuristic only. Header presence is not evidence of exploitability or proof of safety. No network scan occurred.",
      findings,
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "horus-header-review.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function askModel() {
    if (!findings.length || !token.trim() || busy) return;
    setBusy(true); setError(""); setAnswer("");
    try {
      const response = await fetch("/api/lab/ai-review", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Horus-Operator-Token": token.trim() },
        body: JSON.stringify({
          provider,
          findings: findings.map(({ severity, title, detail, recommendation }) => ({ severity, title, detail, recommendation })),
        }),
      });
      const result = await response.json() as { answer?: string; error?: string; model?: string };
      if (!response.ok || !result.answer) throw new Error(result.error || "تعذر الحصول على التحليل.");
      setAnswer(result.answer);
    } catch (err) {
      setError(err instanceof Error ? err.message : "تعذر الاتصال بالنموذج.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface lab-workbench" aria-label="معمل الفحص اليدوي">
      <div className="section-head"><div><span className="eyebrow">LOCAL ANALYSIS · WORKING NOW</span><h2>محرك مراجعة الترويسات الأمنية</h2></div><span className="pill">فحص محلي غير تخريبي</span></div>
      <p className="muted">الصق ترويسات استجابة HTTP لصفحة HTML من بيئة تملكها. التحليل يتم داخل المتصفح دون إرسال بيانات أو إجراء اتصال بالموقع. لا تلصق Cookies أو Authorization أو رموز وصول.</p>
      <div className="lab-toolbar">
        <button type="button" onClick={() => { setRaw(DEMO); setAnswer(""); setError(""); }}>تجربة مثال آمن</button>
        <button type="button" onClick={() => { setRaw(""); setAnswer(""); setError(""); }}>مسح الإدخال</button>
        <button type="button" disabled={!findings.length} onClick={downloadReport}>تصدير تقرير JSON</button>
      </div>
      <label className="lab-label" htmlFor="headers">ترويسات استجابة HTTP</label>
      <textarea id="headers" spellCheck={false} dir="ltr" maxLength={12000} value={raw} onChange={e => {setRaw(e.target.value);setAnswer("");setError("");}} placeholder={"HTTP/2 200 OK\ncontent-type: text/html\nstrict-transport-security: max-age=31536000"} />
      {raw.trim() && !findings.length && <p className="field-note">لم يتم التعرف على ترويسات بصيغة name: value. تأكد من نسخ Response Headers وليس الطلب.</p>}
      <div className="lab-results-head"><strong>النتائج الاسترشادية · {findings.length}</strong><span>{totals.medium} متوسط · {totals.low} منخفض · {totals.info} معلوماتي</span></div>
      <div className="lab-findings" aria-live="polite">
        {findings.length ? findings.map(item => (
          <article key={item.id} className="lab-finding"><div><strong>{item.title}</strong><span className="lab-severity">{item.severity}</span></div><p>{item.detail}</p><small>التوصية: {item.recommendation}</small></article>
        )) : <p className="lab-empty">لا توجد نتائج بعد. استخدم «تجربة مثال آمن» للاختبار الفوري.</p>}
      </div>
      <div className="lab-ai">
        <div><span className="eyebrow">SERVER-SIDE MODEL INTEGRATION</span><h3>تحليل النتائج بواسطة نموذج AI</h3></div>
        <p className="muted">ترسل ملخص النتائج فقط إلى مزود AI الذي تختاره، ولا ترسل الترويسات الخام. الربط جاهز برمجيًا لكنه يحتاج مفاتيح الخادم وضبط صلاحية المشغّل على Vercel.</p>
        <div className="lab-controls">
          <label>المزود<select value={provider} onChange={e => setProvider(e.target.value as "groq" | "openrouter")}><option value="groq">Groq · GPT-OSS 120B</option><option value="openrouter">OpenRouter · نموذج مفتوح الأوزان</option></select></label>
          <label>رمز المشغّل<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} placeholder="LAB_OPERATOR_TOKEN" /></label>
          <button type="button" disabled={!findings.length || !token.trim() || busy} onClick={askModel}>{busy ? "جارٍ تحليل النتائج..." : "تحليل النتائج بالذكاء الاصطناعي"}</button>
        </div>
        {error && <p role="alert" className="lab-error">{error}</p>}
        {answer && <div className="lab-answer" dir="rtl"><strong>مراجعة النموذج</strong><p>{answer}</p></div>}
      </div>
    </section>
  );
}
