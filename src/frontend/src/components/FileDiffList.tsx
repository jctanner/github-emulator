import {Link} from "react-router-dom";

export type DiffFile = {
  filename: string;
  additions: number;
  deletions: number;
  patch: string;
};

function patchLineClass(line: string): string {
  if (line.startsWith("+") && !line.startsWith("+++")) return "addition";
  if (line.startsWith("-") && !line.startsWith("---")) return "deletion";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  return "";
}

export function FileDiffList({
  files,
  fileHref,
}: {
  files: DiffFile[];
  fileHref: (file: DiffFile) => string;
}) {
  return (
    <div className="pr-file-list">
      {files.map((file) => (
        <article className="pr-file" key={file.filename}>
          <header>
            <Link to={fileHref(file)}>{file.filename}</Link>
            <span>
              <span className="diff-additions">+{file.additions}</span>{" "}
              <span className="diff-deletions">-{file.deletions}</span>
            </span>
          </header>
          <pre className="diff-patch">
            {file.patch.split("\n").map((line, index) => (
              <code className={patchLineClass(line)} key={index}>
                {line || " "}
                {"\n"}
              </code>
            ))}
          </pre>
        </article>
      ))}
    </div>
  );
}
