"use client";

import { useState } from "react";
import { supabase } from "../lib/supabase";

type ValidationMetrics = {
  positive_cases?: number;
  control_unlabeled_cases?: number;
  known_site_rediscovery_rate?: number;
  control_high_priority_flag_rate_proxy?: number | null;
  precision_at_high_priority_proxy?: number | null;
  pr_auc_proxy?: number;
  roc_auc_proxy?: number;
  high_priority_count?: number;
};

type ValidationResponse = {
  validation_run_id: string;
  dataset_version: string;
  split: string;
  completed_cases: number;
  total_cases: number;
  metrics: ValidationMetrics;
  scientific_note: string;
};

export default function ValidationPanel() {
  const [split, setSplit] = useState("validation");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runValidation() {
    if (!supabase) {
      setError("Supabase environment is not configured.");
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const { data, error: sessionError } = await supabase.auth.getSession();
      if (sessionError) throw sessionError;
      if (!data.session) throw new Error("Sign in first from the map workspace.");

      const end = new Date();
      const start = new Date();
      start.setUTCFullYear(start.getUTCFullYear() - 2);

      const base = process.env.NEXT_PUBLIC_AI_API_URL ?? "http://localhost:8000";
      const response = await fetch(`${base}/v1/validation/run`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${data.session.access_token}`,
        },
        body: JSON.stringify({
          dataset_version: "unesco-egypt-public-v1-2026-10-27",
          split,
          start: start.toISOString(),
          end: end.toISOString(),
          max_cloud: 20,
          include_unlabeled_controls: true,
        }),
      });

      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Validation failed.");
      setResult(payload as ValidationResponse);
    } catch (validationError) {
      setError(validationError instanceof Error ? validationError.message : "Validation failed.");
    } finally {
      setBusy(false);
    }
  }

  const percent = (value?: number | null) =>
    value === null || value === undefined ? "n/a" : `${(value * 100).toFixed(1)}%`;

  return (
    <section className="validation-panel">
      <div className="validation-title">SCIENTIFIC VALIDATION</div>
      <p className="muted">
        Public known-site rediscovery with geographic holdout and unlabeled controls.
      </p>

      <div className="validation-controls">
        <select value={split} onChange={(event) => setSplit(event.target.value)}>
          <option value="calibration">Calibration</option>
          <option value="validation">Validation</option>
          <option value="blind_test">Blind Test</option>
        </select>
        <button disabled={busy} onClick={runValidation}>
          {busy ? "RUNNING REAL BENCHMARK…" : "RUN BENCHMARK"}
        </button>
      </div>

      {error && <div className="validation-error">{error}</div>}

      {result && (
        <div className="validation-results">
          <div>
            <span>Known-site rediscovery</span>
            <strong>{percent(result.metrics.known_site_rediscovery_rate)}</strong>
          </div>
          <div>
            <span>Precision @ High Priority</span>
            <strong>{percent(result.metrics.precision_at_high_priority_proxy)}</strong>
          </div>
          <div>
            <span>PR-AUC proxy</span>
            <strong>
              {result.metrics.pr_auc_proxy === undefined
                ? "n/a"
                : result.metrics.pr_auc_proxy.toFixed(3)}
            </strong>
          </div>
          <div>
            <span>Control flag rate</span>
            <strong>{percent(result.metrics.control_high_priority_flag_rate_proxy)}</strong>
          </div>
          <small>
            {result.completed_cases}/{result.total_cases} cases · {result.split} · controls are
            unlabeled, not confirmed negatives.
          </small>
        </div>
      )}
    </section>
  );
}
