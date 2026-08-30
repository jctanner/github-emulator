import {FormEvent, useState} from "react";
import {useNavigate} from "react-router-dom";

import {api} from "../api/client";

export function NewRepositoryPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPrivate, setPrivate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const {data, response} = await api.POST("/api/v3/user/repos", {
      body: {name, description, private: isPrivate, auto_init: true},
    });
    if (!data || !response.ok) return setError("Could not create repository.");
    await navigate(`/${data.full_name}`);
  }
  return (
    <form className="editor-form" onSubmit={(event) => void submit(event)}>
      <h1>Create a new repository</h1>
      {error ? <p className="flash-error">{error}</p> : null}
      <label>
        Repository name
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        Description
        <input
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={isPrivate}
          onChange={(event) => setPrivate(event.target.checked)}
        />{" "}
        Private repository
      </label>
      <button className="button" type="submit">
        Create repository
      </button>
    </form>
  );
}
