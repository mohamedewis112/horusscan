import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 15;
export const dynamic = "force-dynamic";

type Service = { status: "ready" | "missing_key" | "unreachable" | "paused" | "not_installed" | "model_missing"; label: string; detail: string };
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
      ? { status: "ready", label: "Groq متصل", detail: "تم توثيق المفتاح وظهور GPT-OSS 120B في قائمة النماذج؛ لم يُختبر توليد الرد بعد." }
      : { status: "model_missing", label: "Groq متصل والنموذج غير متاح", detail: "تم الاتصال بـGroq لكن النموذج المحدد لم يظهر في القائمة." };
  } catch {
    return { status: "unreachable", label: "تعذر الوصول إلى Groq", detail: "فشل التحقق الخارجي أو انتهت المهلة. أعد الاختبار." };
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET() {
  const now = Date.now();
  if (!lastCheck || now >= lastCheck.expiresAt) {
    const model = await inspectModel();
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
        executor: {
          status: "not_installed",
          label: "ARTEX غير مثبتة",
          detail: "لم يتم توصيل محرك تنفيذ للاختبارات بعد؛ التحليل المحلي للترويسات يعمل بشكل مستقل.",
        },
        test_scope: "هذا فحص اتصال لخدمة Groq فقط؛ لا يقوم بمسح مواقع أو تشغيل ARTEX.",
      },
    };
  }
  return NextResponse.json(lastCheck.value, { headers });
}
