import type {components} from "../api/schema";

type Label = Pick<
  components["schemas"]["LabelResponse"],
  "name" | "color" | "description"
>;

export function LabelPill({label}: {label: Label}) {
  return (
    <span
      className="label-pill"
      style={{backgroundColor: `#${label.color}`}}
      title={label.description ?? undefined}
    >
      {label.name}
    </span>
  );
}
