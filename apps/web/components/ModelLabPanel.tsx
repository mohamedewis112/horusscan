"use client";

import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

type ModelState = {
  key: string;
  role: string;
  modalities: string[];
  available: boolean;
  reason?: string | null;
};

type Ranking = {
  model_key: string;
  status: string;
  composite_validation_score: number | null;
  metrics: Record<string, unknown>;
  availability_reason?: string | null;
};

export default function ModelLabPanel() {
  const [models, setModels] = useState<ModelState[]>([]);
  const [ranking, setRanking] = useState<Ranking[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
    fetch(`${base}/v1/foundation/models`)
      .then((response) => response.json())
      .then((payload) => setModels(payload.models ?? []))
      .catch((modelError) =>
        setError(modelError instanceof Error ? modelError.message : "Could not load models.")
      );
  }, []);

  async function runTournament() {
    if (!supabase) {
      setError("Supabase is not configured.");
      return;
    }
    setBusy(true);
    setError(null);
    setRanking([]);
    try {
      const { data, error: sessionError } = await supabase.auth.getSession();
      if (sessionError) throw sessionError;
      if (!data.session) throw new Error("Sign in first.");

      const end = new Date();
      const start = new Date();
      start.setUTCFullYear(start.getUTCFullYear() - 2);

      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const response = await fetch(`${base}/v1/tournament/run`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${data.session.access_token}`,
        },
        body: JSON.stringify({
          dataset_version: "unesco-egypt-public-v1-2026-10-27",
          split: "validation",
          start: start.toISOString(),
          end: end.toISOString(),
          max_cloud: 20,
          include_baseline: true,
          include_unlabeled_controls: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Tournament failed.");
      setRanking(payload.ranking ?? []);
    } catch (tournamentError) {
      setError(tournamentError instanceof Error ? tournamentError.message : "Tournament failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="model-lab">
      <div className="validation-title">MODEL LAB · HYPERFUSION</div>
      <p className="muted">
        Real model endpoints only. Unconfigured models remain unavailable and never emit fake scores.
      </p>

      <div className="model-grid">
        {models.map((model) => (
          <div className="model-chip" key={model.key}>
            <span>{model.key}</span>
            <strong className={model.available ? "online" : "offline"}>
              {model.available ? "READY" : "OFFLINE"}
            </strong>
            <small>{model.role}</small>
          </div>
        ))}
      </div>

      <button className="tournament-button" disabled={busy} onClick={runTournament}>
        {busy ? "RUNNING MODEL TOURNAMENT…" : "RUN VALIDATION TOURNAMENT"}
      </button>

      {error && <div className="validation-error">{error}</div>}

      {ranking.length > 0 && (
        <div className="tournament-ranking">
          {ranking.map((row, index) => (
            <div key={row.model_key}>
              <b>#{index + 1} {row.model_key}</b>
              <span>{row.status}</span>
              <strong>
                {row.composite_validation_score === null
                  ? "n/a"
                  : row.composite_validation_score.toFixed(3)}
              </strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
