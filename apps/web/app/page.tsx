import HorusMap from "../components/HorusMap";
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
            <b>Milestone 3 · Scientific Validation</b>
            <span className="muted">
              Multisensor analysis with geographic holdout, known-site rediscovery,
              unlabeled controls, and a reserved final-test split.
            </span>
          </div>
          <ValidationPanel />
          <ModelLabPanel />
        </aside>
        <HorusMap />
      </section>
    </main>
  );
}
