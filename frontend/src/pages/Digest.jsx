export default function Digest() {
  return (
    <section className="max-w-2xl mx-auto bg-card border border-line rounded-md p-8">
      <p className="label-mono text-olive">Operator-only preview</p>
      <h2 className="font-display text-3xl mt-2">Digest previews are server-gated.</h2>
      <p className="text-sm text-ink-soft mt-3">
        The public site never bundles the operator bearer or exposes send controls.
      </p>
    </section>
  );
}
