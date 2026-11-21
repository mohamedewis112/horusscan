import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 15;
export const dynamic = "force-dynamic";

type Service = { status: "ready" | "missing_key" | "unreachable" | "paused" | "not_installed" | "model_missing"; label: string; detail: string; url?: string };
type Health = { checked_at: string; model: Service; database: Service; executor: Service; test_scope: string };
let lastCheck: { expiresAt: number; value: Health } | undefined;

const headers = {
  "Cache-Control": "private, no-store, max-age=0",
  "X-Content-Type-Options": "nosniff",
};

async function inspectModel(): Promise<Service> {
  const key = process.env.GROQ_API_KEY;
  if (!key) return { status: "missing_key", label: "مفتاح Groq غير مُعد", detail: "أضف GROQ_API_KEY لمشروع Vercel الحالي." };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 7000);
  try {
    const response = await fetch("https://api.groq.com/openai/v1/models", {
      headers: { Authorization: "Bearer " + key },
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) return {
      status: "unreachable",
      label: "Groq لم يؤكد الاتصال",
      detail: response.status === 401 || response.status === 403
        ? "مفتاح Groq مرفوض. راجع المفتاح أو صلاحياته."
        : response.status === 429 ? "تم تجاوز حد طلبات Groq." : "تعذر التحقق من نماذج Groq حاليًا.",
    };
    const payload: unknown = await response.json();
    const data = payload && typeof payload === "object" && "data" in payload ? (payload as { data: unknown }).data : null;
    const available = Array.isArray(data) && data.some((x) =>
      x && typeof x === "object" && "id" in x && x.id === "openai/gpt-oss-120b");
    return available
      ? { status: "ready", label: "Groq متصل", detail: "تم توثيق مفتاح Groq للوحة عين حورس؛ ربط نموذج ARTEX المستقل يحتاج الإعداد من داخل ARTEX." }
      : { status: "model_missing", label: "Groq متصل والنموذج غير متاح", detail: "تم الاتصال بـGroq لكن النموذج المحدد لم يظهر في القائمة." };
  } catch {
    return { status: "unreachable", label: "تعذر الوصول إلى Groq", detail: "فشل التحقق الخارجي أو انتهت المهلة. أعد الاختبار." };
  } finally {
    clearTimeout(timeout);
  }
}


async function inspectExecutor(): Promise<Service> {
  const url = process.env.HORUS_ARTEX_SANDBOX_URL?.replace(/\/+$/, "");
  if (!url || !/^https:\/\/sb-[a-z0-9-]+\.vercel\.run$/.test(url)) {
    return {
      status: "not_installed",
      label: "ARTEX غير مرتبطة بهذه النسخة",
      detail: "لم يُسجّل عنوان Sandbox صالح في إعدادات Vercel.",
    };
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4500);
  try {
    const response = await fetch(url + "/setup", { signal: controller.signal, redirect: "manual", cache: "no-store" });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      return { status: "ready", label: "ARTEX متاحة في Sandbox", detail: "الواجهة تتجاوب؛ يلزم تسجيل الدخول. Sandbox مؤقتة وقد تتوقف تلقائيًا.", url };
    }
    if (response.ok) {
      const text = (await response.text()).slice(0, 4500);
      if (text.includes("ARTEX") || text.includes("Artex")) {
        return { status: "ready", label: "ARTEX متاحة في Sandbox", detail: "محرك ARTEX يعمل، والتجربة الداخلية فقط. ربط النموذج بوكلاء ARTEX يحتاج ضبطًا مستقلًا.", url };
      }
    }
    return { status: "unreachable", label: "ARTEX مثبتة لكن الاستجابة غير مؤكدة", detail: "البيئة موجودة، ولكن فحصها الخارجي لم يثبت جاهزيتها حاليًا.", url };
  } catch {
    return { status: "unreachable", label: "ARTEX المثبتة غير متاحة حاليًا", detail: "قد تكون جلسة Vercel Sandbox توقفت تلقائيًا؛ تحتاج استعادة الجلسة.", url };
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET() {
  const now = Date.now();
  if (!lastCheck || now >= lastCheck.expiresAt) {
    const [model, executor] = await Promise.all([inspectModel(), inspectExecutor()]);
    lastCheck = {
      expiresAt: Date.now() + 300_000,
      value: {
        checked_at: new Date().toISOString(),
        model,
        database: {
          status: "paused",
          label: "Supabase متوقفة",
          detail: "مشروع HorusScan الأصلي متوقف. رفضت Supabase استعادته بسبب حد المشروعين المجانيين.",
        },
        executor,
        test_scope: "يتم فحص اتصال Groq وواجهة ARTEX فقط؛ لا يُشغّل هذا المسار أي هجوم أو مسح لموقع.",
      },
    };
  }
  return NextResponse.json(lastCheck.value, { headers });
}
