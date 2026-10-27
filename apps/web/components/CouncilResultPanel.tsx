"use client";

type CouncilCandidate = {
  candidate_id: string;
  approximate_center: { lon: number; lat: number; precision: string };
  geometry: { type: "Polygon"; coordinates: number[][][] };
  evidence_confidence_index: number;
  confidence_kind: string;
  uncertainty_band: { low: number; high: number };
  spatial_uncertainty_m: number;
  priority: string;
  supporters: string[];
  challengers: string[];
  neutral: string[];
  branch_scores: Record<string, number>;
  independent_support_count: number;
  independent_groups_present: number;
  consensus_matrix: Array<{
    member: string;
    independence_group: string;
    verdict: string;
    evidence_score: number;
  }>;
  critic: {
    alternative_natural_explanations: string[];
    contradiction_ratio: number;
    branch_dispersion: number;
    verdict: string;
  };
  council_summary: {
    support_count: number;
    challenge_count: number;
    neutral_count: number;
    model_agreement: string;
  };
  multi_scale_review?: {
    round_count: number;
    passed_rounds: number;
    stability_score: number;
    verdict: string;
    rounds: Array<{
      round: string;
      radius_cells: number;
      local_score: number;
      percentile: number;
      accepted: boolean;
    }>;
  };
  archaeology_memory?: {
    status: string;
    fingerprint_count: number;
    memory_similarity_score: number;
    memory_verdict: string;
    necropolis_context_score: number;
    necropolis_verdict: string;
    top_reference_matches: Array<{
      slug: string;
      name: string;
      archetype: string;
      site_type: string;
      similarity: number;
      shared_branch_count: number;
      distance_km_context_only: number;
      public_source: string;
    }>;
    top_archetypes: Array<{
      archetype: string;
      similarity: number;
      reference_count: number;
    }>;
    calibrated: boolean;
    confidence_adjustment: number;
    distance_used_in_similarity: boolean;
  };
};

export type HorusCouncil = {
  version: string;
  fusion_profile: string;
  candidate_count: number;
  coverage_summary?: {
    valid_pixels: number;
    total_pixels: number;
    coverage_fraction: number;
    branch_count: number;
    independent_group_count: number;
    independent_groups: string[];
    review_rounds: number;
    score_distribution?: Record<string, number>;
    area_share_above_absolute_score?: Record<string, number>;
  };
  memory_layer?: {
    status: string;
    reference_site_count: number;
    fingerprint_count: number;
    dataset_version: string;
    adjudication_only: boolean;
    confidence_adjustment: number;
  };
  candidates: CouncilCandidate[];
  method: Record<string, string>;
};

const LABELS: Record<string, string> = {
  spectral: "الطيف البصري",
  sar: "الرادار SAR",
  terrain: "التضاريس",
  thermal: "الحرارة",
  olmoearth_2025: "OlmoEarth",
  terramind: "TerraMind",
  prithvi_eo_2: "Prithvi EO 2.0",
  dinov3_satellite: "DINOv3 Satellite",
  spectralearth_fm: "SpectralEarth-FM",
  terraflow: "TerraFlow",
  physics_sar: "Physics-SAR",
  thermal_inertia: "Thermal Inertia",
  archaeology_memory: "ذاكرة الآثار المصرية",
  necropolis_context: "سياق المقابر والجبانات",
};

const VERDICT: Record<string, string> = {
  support: "يؤيد",
  neutral: "محايد",
  challenge: "يعترض",
};

const PRIORITY: Record<string, string> = {
  very_high: "مرتفعة جدًا",
  high: "مرتفعة",
  medium: "متوسطة",
  low: "منخفضة",
};

const ALTERNATIVES: Record<string, string> = {
  surface_or_vegetation_change: "قد يكون تغيرًا سطحيًا أو نباتيًا",
  surface_temperature_or_moisture_effect: "قد يكون تأثير حرارة أو رطوبة سطحية",
  natural_geomorphology: "قد يكون شكلًا جيومورفولوجيًا طبيعيًا",
  surface_roughness_or_radar_scattering: "قد يكون خشونة سطح أو تشتتًا راداريًا",
  foundation_representation_anomaly_without_physical_agreement: "النموذج التأسيسي يرى شذوذًا دون دعم فيزيائي مستقل كافٍ",
};

export default function CouncilResultPanel({
  council,
}: {
  council: HorusCouncil;
}) {
  if (!council.candidates.length) {
    return (
      <section className="council-panel">
        <strong>مجلس Horus لم يجد مرشحًا قويًا داخل المنطقة الحالية.</strong>
      </section>
    );
  }

  return (
    <section className="council-panel" dir="rtl">
      <div className="council-heading">
        <div>
          <span className="eyebrow">HORUS COUNCIL · القرار المشترك</span>
          <h3>أهم المناطق المرشحة داخل نطاق التحليل</h3>
        </div>
        <span className="council-count">{council.candidate_count} مرشح</span>
      </div>

      <p className="council-note">
        النسبة التالية هي <b>مؤشر ثقة في الأدلة</b> بعد اتفاق/اعتراض الفروع المختلفة،
        وليست احتمالًا مؤكدًا لوجود أثر أو مبنى مدفون.
      </p>

      {council.memory_layer && (
        <div className="memory-layer-summary">
          <div>
            <span>بنك المراجع الأثرية</span>
            <strong>{council.memory_layer.reference_site_count} موقع عام</strong>
          </div>
          <div>
            <span>بصمات Sensor جاهزة</span>
            <strong>{council.memory_layer.fingerprint_count}</strong>
          </div>
          <div>
            <span>دور الطبقة</span>
            <strong>مراجعة فقط</strong>
          </div>
        </div>
      )}

      {council.coverage_summary && (
        <div className="coverage-summary">
          <div>
            <span>تغطية التحليل</span>
            <strong>{Math.round(council.coverage_summary.coverage_fraction * 100)}%</strong>
          </div>
          <div>
            <span>الخلايا التي تمت مراجعتها</span>
            <strong>{council.coverage_summary.valid_pixels.toLocaleString("ar-EG")}</strong>
          </div>
          <div>
            <span>عائلات أدلة مستقلة</span>
            <strong>{council.coverage_summary.independent_group_count}</strong>
          </div>
          <div>
            <span>جولات المراجعة المكانية</span>
            <strong>{council.coverage_summary.review_rounds}</strong>
          </div>
        </div>
      )}

      <div className="candidate-list">
        {council.candidates.map((candidate) => (
          <article
            key={candidate.candidate_id}
            className="candidate-card"
          >
            <div className="candidate-topline">
              <strong>{candidate.candidate_id}</strong>
              <span className={"priority " + candidate.priority}>
                أولوية {PRIORITY[candidate.priority] ?? candidate.priority}
              </span>
            </div>

            <div className="confidence-row">
              <div>
                <span>مؤشر الثقة في الأدلة</span>
                <strong>{candidate.evidence_confidence_index.toFixed(1)}%</strong>
              </div>
              <small>
                نطاق عدم اليقين {candidate.uncertainty_band.low.toFixed(1)}–
                {candidate.uncertainty_band.high.toFixed(1)}%
              </small>
            </div>

            <div className="evidence-meter">
              <span style={{ width: Math.min(100, Math.max(0, candidate.evidence_confidence_index)) + "%" }} />
            </div>

            <div className="candidate-facts">
              <span>
                أدلة مستقلة: {candidate.independent_support_count}/{candidate.independent_groups_present}
              </span>
              <span>عدم يقين مكاني: ±{Math.round(candidate.spatial_uncertainty_m)} م</span>
              <span>اتفاق المجلس: {candidate.council_summary.model_agreement}</span>
              {candidate.multi_scale_review && (
                <span>
                  ثبات المرشح عبر المراجعات: {candidate.multi_scale_review.passed_rounds}/{candidate.multi_scale_review.round_count}
                </span>
              )}
            </div>

            {candidate.archaeology_memory?.status === "ready" && (
              <div className="memory-review-card">
                <div className="memory-review-head">
                  <div>
                    <span>Egypt Archaeology Memory</span>
                    <strong>
                      تشابه مرجعي {Math.round(candidate.archaeology_memory.memory_similarity_score * 100)}%
                    </strong>
                  </div>
                  <div>
                    <span>Necropolis Context</span>
                    <strong>
                      {Math.round(candidate.archaeology_memory.necropolis_context_score * 100)}%
                    </strong>
                  </div>
                </div>

                <div className="memory-archetypes">
                  <b>أقرب الأنماط الأثرية:</b>
                  {candidate.archaeology_memory.top_archetypes.slice(0, 3).map((row) => (
                    <span key={row.archetype}>
                      {row.archetype} · {Math.round(row.similarity * 100)}%
                    </span>
                  ))}
                </div>

                <div className="memory-matches">
                  <b>أقرب مواقع مرجعية:</b>
                  {candidate.archaeology_memory.top_reference_matches.slice(0, 3).map((row) => (
                    <span key={row.slug}>
                      {row.name} · {Math.round(row.similarity * 100)}%
                    </span>
                  ))}
                </div>

                <small>
                  المسافة من المواقع المعروفة لا تدخل في التشابه، والطبقة لا تغيّر الثقة الرقمية حتى يتم Calibration مستقل.
                </small>
              </div>
            )}

            <div className="consensus-grid">
              {candidate.consensus_matrix.map((item) => (
                <div key={item.member} className={"consensus-item " + item.verdict}>
                  <b>{LABELS[item.member] ?? item.member}</b>
                  <span>{VERDICT[item.verdict] ?? item.verdict}</span>
                  <small>{Math.round(item.evidence_score * 100)}%</small>
                </div>
              ))}
            </div>

            {candidate.critic.alternative_natural_explanations.length > 0 ? (
              <div className="critic-box">
                <b>اعتراض المجلس:</b>{" "}
                {candidate.critic.alternative_natural_explanations
                  .map((item) => ALTERNATIVES[item] ?? item)
                  .join(" · ")}
              </div>
            ) : (
              <div className="critic-box positive">
                لا يوجد تفسير طبيعي بديل قوي من الفروع الحالية.
              </div>
            )}

            <div className="candidate-location">
              مركز منطقة البحث التقريبي: {candidate.approximate_center.lat.toFixed(2)}, {candidate.approximate_center.lon.toFixed(2)}
              <small> — ليست إحداثيات حفر دقيقة</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
