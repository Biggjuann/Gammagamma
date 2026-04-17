import Dashboard from "@/components/Dashboard";

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Real-time gamma exposure intelligence
        </h1>
        <p className="text-sm text-slate-400 max-w-2xl">
          Structural levels, regime detection, and five original signals built for options
          traders who want to see what dealers are forced to do. Consolidated OPRA data
          delivered via Databento, recomputed every 60 seconds.
        </p>
      </section>
      <Dashboard />
    </div>
  );
}
