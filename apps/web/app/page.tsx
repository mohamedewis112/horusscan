"use client";

import { useMemo, useState } from "react";
import SecurityWorkbench from "../components/SecurityWorkbench";

type Phase = "scope" | "model" | "environment" | "review";
const phases: { id: Phase; title: string; detail: string }[] = [
  { id: "scope", title: "إثبات الملكية ونطاق الاختبار", detail: "نطاق مصرح به، بدون اختبار موقع عام تلقائيًا" },
  { id: "model", title: "ربط نموذج مفتوح الأوزان", detail: "Groq أو OpenRouter من خلال الخادم فقط" },
  { id: "environment", title: "إنشاء معمل معزول", detail: "بيانات تجريبية ونسخة قابلة للاستعادة" },
  { id: "review", title: "فحوصات وتقرير أدلة", detail: "أوامر محددة مع موافقة بشرية وسجل تدقيق" },
];

const tools = [
  { name: "فحص الكود والاعتمادات", about: "SAST واكتشاف الحزم المعرضة للثغرات", stage: "جاهز للتصميم" },
  { name: "إعدادات HTTP وTLS", about: "فحص غير تخريبي بعد التحقق من النطاق", stage: "غير موصل" },
  { name: "اختبار ثغرات داخل المعمل", about: "استغلال محاكى في بيئة منعزلة وبيانات وهمية", stage: "غير موصل" },
  { name: "تقارير الإصلاح", about: "أدلة قابلة للمراجعة، درجة خطورة، وخطوات علاج", stage: "غير موصل" },
];

export default function HomePage() {
  const [target, setTarget] = useState("");
  const [ack, setAck] = useState(false);
  const [isolated, setIsolated] = useState(true);
  const [model, setModel] = useState("groq");
  const [showPlan, setShowPlan] = useState(false);
  const [tab, setTab] = useState<Phase>("scope");
  const validTarget = useMemo(() => {
    try {
      const url = new URL(target);
      return url.protocol === "https:" && !!url.hostname && !url.username && !url.password;
    } catch {
      return false;
    }
  }, [target]);
  const plan = {
    application: "Horus Security Lab",
    source_repository: "mohamedewis112/horusscan",
    authorized_target_candidate: validTarget ? new URL(target).origin : null,
    verification_status: "pending",
    provider_preference: model,
    execution_environment: isolated ? "isolated_staging_only" : "not_allowed",
    exploitation_mode: "disabled_until_ownership_and_isolation_verified",
    human_approval_required: true,
    status: "configuration_only_no_scan_executed",
  };
  const canPrepare = validTarget && ack && isolated;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="identity"><span className="identity-icon">◈</span><div><strong>HORUS</strong><small>SECURITY LAB</small></div></div>
        <p className="sidebar-label">إدارة المعمل</p>
        <nav aria-label="المراحل">
          {phases.map((phase, index) => (
            <button key={phase.id} className={tab === phase.id ? "navlink selected" : "navlink"} type="button" onClick={() => setTab(phase.id)}>
              <span className="step-index">0{index + 1}</span><span>{phase.title}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom"><span className="dot" /> نسخة التطوير · دون تشغيل اختبارات</div>
      </aside>
      <main className="main">
        <div className="topline"><span>HORUSSCAN / SECURITY LAB</span><span className="top-status">معاينة فقط · Preview</span></div>
        <section className="hero">
          <div className="eyebrow">نفس مشروع عين حورس · استخدام جديد</div>
          <h1>منصة اختبار أمان<br/><em>تحت سيطرتك.</em></h1>
          <p>إعداد نطاق اختبار مصرح به، تشغيل وكلاء AI عبر خدمة سحابية، وتجارب أمنية في معمل معزول. الواجهة الحالية مرحلة تجهيز؛ لم يُربط محرك تنفيذ أو يُشغّل أي فحص.</p>
          <div className="hero-facts"><span>GitHub: horusscan</span><span>Vercel: existing projects</span><span>Supabase: paused</span></div>
        </section>
        <section className="metrics" aria-label="حالة التكامل">
          <div className="metric"><small>ARTEX EXECUTOR</small><strong>غير متصل</strong><span>لا توجد عمليات فحص</span></div>
          <div className="metric"><small>AI MODEL</small><strong>غير متصل</strong><span>يلزم مفتاح API على الخادم</span></div>
          <div className="metric"><small>DATABASE</small><strong>متوقفة</strong><span>قاعدة HorusScan الأصلية</span></div>
        </section>
        <div className="content-grid">
          <section className="surface configure">
            <div className="section-head"><div><span className="eyebrow">01 / INITIAL CONFIGURATION</span><h2>تجهيز نطاق الاختبار</h2></div><span className="pill">لا يُنفذ أي اتصال</span></div>
            <label htmlFor="target">عنوان النسخة التجريبية الخاصة بك</label>
            <input id="target" type="url" dir="ltr" spellCheck={false} value={target} onChange={e => {setTarget(e.target.value);setShowPlan(false);}} placeholder="https://staging.example.com" />
            <p className="field-note">لا تضف نطاق إنتاج عليه بيانات حقيقية. العنوان هنا للتخطيط فقط، وليس تصريحًا آليًا بالاختبار.</p>
            <label htmlFor="model">مزود النموذج (اختيار مبدئي)</label>
            <select id="model" value={model} onChange={e => {setModel(e.target.value);setShowPlan(false);}}><option value="groq">Groq · نموذج مفتوح الأوزان</option><option value="openrouter">OpenRouter · نماذج مفتوحة</option><option value="self_hosted">خادم نماذج خاص (لاحقًا)</option></select>
            <label className="check"><input type="checkbox" checked={isolated} onChange={e => {setIsolated(e.target.checked);setShowPlan(false);}} /><span>تنفيذ أي تجارب مؤثرة على بيئة معزولة، ببيانات وهمية وبنسخة استعادة</span></label>
            <label className="check"><input type="checkbox" checked={ack} onChange={e => {setAck(e.target.checked);setShowPlan(false);}} /><span>أقر أنني أملك النطاق أو لدي تصريح مكتوب، وأن إعداد المعمل لا يجيز فحص موقع عام تلقائيًا</span></label>
            <button className="primary-action" type="button" disabled={!canPrepare} onClick={() => setShowPlan(true)}>إنشاء خطة التجهيز <span>←</span></button>
            {showPlan && <div className="plan"><strong>خطة جاهزة للمراجعة — لم يبدأ أي فحص</strong><pre dir="ltr">{JSON.stringify(plan, null, 2)}</pre><button className="subtle-button" type="button" onClick={() => {void navigator.clipboard?.writeText(JSON.stringify(plan, null, 2));}}>نسخ الخطة</button></div>}
          </section>
          <section className="surface roadmap">
            <div className="section-head"><div><span className="eyebrow">IMPLEMENTATION PATH</span><h2>{phases.find(p => p.id === tab)?.title}</h2></div></div>
            <p className="muted">{phases.find(p => p.id === tab)?.detail}</p>
            <div className="timeline">
              {phases.map((phase, index) => <div className={phase.id === tab ? "timeline-item on" : "timeline-item"} key={phase.id}><span className="number">0{index+1}</span><div><strong>{phase.title}</strong><small>{phase.detail}</small></div></div>)}
            </div>
            <div className="notice"><b>ضوابط التشغيل</b><p>لا استغلال أو إغراق طلبات أو حذف بيانات على موقع منشور. التجارب المؤثرة تُنفّذ فقط داخل معمل معزول بعد التحقق من الملكية والنطاق.</p></div>
          </section>
        </div>
        <section className="surface capabilities"><div className="section-head"><div><span className="eyebrow">SECURITY WORKFLOWS</span><h2>الاختبارات المخطط لها</h2></div><span className="pill">قيد التطوير</span></div>
        <div className="capability-grid">{tools.map((item,index) => <div className="capability" key={item.name}><span className="cap-number">0{index+1}</span><h3>{item.name}</h3><p>{item.about}</p><span className="cap-status">{item.stage}</span></div>)}</div></section>
        <SecurityWorkbench />
        <footer>HORUS SECURITY LAB · من نفس مستودع HorusScan · لا توجد نتائج أو فحوصات فعلية في هذه النسخة</footer>
      </main>
    </div>
  );
}
