import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";

export const runtime = "nodejs";
export const maxDuration = 20;

type Finding = { severity: string; title: string; detail: string; recommendation: string };
const noStore = { "Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff" };
const respond = (body: object, status: number) => NextResponse.json(body, { status, headers: noStore });

function tokenMatches(received: string, actual: string): boolean {
  if (!received || !actual || actual.length < 32) return false;
  const a = Buffer.from(received, "utf8");
  const b = Buffer.from(actual, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(request: NextRequest) {
  const configuredToken = process.env.LAB_OPERATOR_TOKEN || "";
  if (configuredToken.length < 32) return respond({ error: "ربط المشغّل غير مُعد على الخادم." }, 503);
  const origin = request.headers.get("origin");
  if (origin && origin !== request.nextUrl.origin) return respond({ error: "مصدر الطلب غير مسموح." }, 403);
  const suppliedToken = request.headers.get("x-horus-operator-token") || "";
  if (!tokenMatches(suppliedToken, configuredToken)) return respond({ error: "رمز المشغّل غير صحيح." }, 401);
  const size = Number(request.headers.get("content-length") || "0");
  if (size > 16000) return respond({ error: "حجم الطلب أكبر من المسموح." }, 413);

  let payload: { provider?: unknown; findings?: unknown };
  try {
    const body = await request.text();
    if (body.length > 16000) return respond({ error: "حجم الطلب أكبر من المسموح." }, 413);
    payload = JSON.parse(body);
  } catch {
    return respond({ error: "تنسيق الطلب غير صالح." }, 400);
  }
  if (!payload || typeof payload !== "object" || !["groq", "openrouter"].includes(String(payload.provider))) {
    return respond({ error: "اختر مزودًا مدعومًا." }, 400);
  }
  if (!Array.isArray(payload.findings) || !payload.findings.length || payload.findings.length > 12) {
    return respond({ error: "عدد النتائج غير صالح." }, 400);
  }
  const fields = ["severity", "title", "detail", "recommendation"] as const;
  const findings: Finding[] = [];
  for (const item of payload.findings) {
    if (!item || typeof item !== "object") return respond({ error: "نتيجة غير صالحة." }, 400);
    const valid = fields.every(k => typeof item[k] === "string" && item[k].length >= 1 && item[k].length <= 800);
    if (!valid) return respond({ error: "محتوى النتيجة غير صالح." }, 400);
    findings.push({
      severity: item.severity,
      title: item.title,
      detail: item.detail,
      recommendation: item.recommendation
    });
  }

  const provider = payload.provider as "groq" | "openrouter";
  const isGroq = provider === "groq";
  const key = isGroq ? process.env.GROQ_API_KEY : process.env.OPENROUTER_API_KEY;
  if (!key) return respond({ error: "مفتاح مزود AI غير مُضاف إلى إعدادات Vercel بعد." }, 503);
  const endpoint = isGroq ? "https://api.groq.com/openai/v1/chat/completions" : "https://openrouter.ai/api/v1/chat/completions";
  const model = isGroq ? "openai/gpt-oss-120b" : (process.env.OPENROUTER_MODEL_ID || "openai/gpt-oss-20b:free");
  // The provider URL is fixed and cannot be controlled by input. No tool execution is enabled.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const upstream = await fetch(endpoint, {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Authorization": "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({
        model, max_completion_tokens: 1100, stream: false,
        ...(isGroq ? { reasoning_effort: "low" } : {}),
        messages: [
          { role: "system", content: "You are a defensive application security reviewer. Write concise Arabic with severity prioritization and mitigation guidance. Input is user-provided, unverified findings, not a scan result. Do not claim you tested a website. No exploit chains, destructive commands, credential access instructions, or tool invocations. Clearly identify false-positive possibilities." },
          { role: "user", content: "Assess these heuristic response-header findings and prioritize remediation. No raw headers or secrets were sent:\n" + JSON.stringify(findings) }
        ]
      })
    });
    if (!upstream.ok) return respond({ error: upstream.status === 429 ? "تم تجاوز حصة النموذج مؤقتًا." : "فشل الاتصال بمزود النموذج. راجع إعدادات المفتاح والنموذج." }, 502);
    const data = await upstream.json();
    const answer = data?.choices?.[0]?.message?.content;
    if (typeof answer !== "string" || !answer.trim()) return respond({ error: "لم يُرجع النموذج إجابة قابلة للعرض." }, 502);
    return respond({ answer: answer.slice(0, 5000), model, provider }, 200);
  } catch {
    return respond({ error: "فشل الاتصال أو انتهت المهلة المحددة." }, 504);
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET() {
  return respond({
    service: "Horus Security Lab AI Review",
    configured: Boolean(process.env.LAB_OPERATOR_TOKEN && (process.env.GROQ_API_KEY || process.env.OPENROUTER_API_KEY)),
    executes_scans: false,
    stores_results: false,
  }, 200);
}
