import dynamic from "next/dynamic";

const HorusMap = dynamic(() => import("../components/HorusMap"), { ssr: false });

export default function HomePage() {
  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">HORUSSCAN AI</div>
        <div className="tag">EARTH OBSERVATION · ARCHAEOLOGICAL INTELLIGENCE</div>
      </header>
      <section className="workspace">
        <aside className="panel">
          <h1>See patterns. Understand the land. Protect the past.</h1>
          <p className="muted">
            Scientific decision support for remote archaeological prospection.
            Results are evidence-based candidates, not excavation confirmation.
          </p>
          <div className="status">
            <b>Milestone 1</b>
            <span className="muted">
              Draw an AOI, discover real Sentinel-2 scenes, process multispectral
              data, and generate a reproducible anomaly layer.
            </span>
          </div>
        </aside>
        <HorusMap />
      </section>
    </main>
  );
}
