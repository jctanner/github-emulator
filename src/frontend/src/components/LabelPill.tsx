import type {components} from "../api/schema";

type Label = components["schemas"]["LabelResponse"];

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
