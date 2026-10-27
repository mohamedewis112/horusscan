import HorusMapV2 from "../components/HorusMapV2";
import ValidationPanel from "../components/ValidationPanel";
import ModelLabPanel from "../components/ModelLabPanel";

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
            <b>Milestone 4 · Horus Council</b>
            <span className="muted">
              Multi-model evidence council with calibrated fusion, structured disagreement, known-site validation, and a reserved final-test split.
            </span>
          </div>
          <ValidationPanel />
          <ModelLabPanel />
        </aside>
        <HorusMapV2 />
      </section>
    </main>
  );
}
