import {Link} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Repository = components["schemas"]["RepoResponse"];

export function DashboardPage() {
  const repositories = useApiData<Repository[]>("repositories", async () => {
    const {data, response} = await api.GET("/api/v3/repositories", {});
    return [
      ...requireApiData(data, response, "Could not load repositories."),
    ].sort((left, right) =>
      left.full_name.localeCompare(right.full_name, undefined, {
        sensitivity: "base",
      }),
    );
  });

  return (
    <Loadable loading={repositories.loading} error={repositories.error}>
      <section>
        <div className="page-heading">
          <div>
            <h1>Repositories</h1>
            <p className="muted">
              Repositories in the emulator, sorted by name
            </p>
          </div>
          <Link className="button" to="/new">
            New repository
          </Link>
        </div>
        <div className="list-box">
          {repositories.data?.map((repository) => (
            <article className="list-row" key={repository.id}>
              <h2>
                <Link to={`/${repository.full_name}`}>
                  {repository.full_name}
                </Link>
                <span className="badge">{repository.visibility}</span>
              </h2>
              {repository.description ? <p>{repository.description}</p> : null}
              <p className="muted">
                Default branch: {repository.default_branch}
              </p>
            </article>
          ))}
          {repositories.data?.length === 0 ? (
            <p className="empty">No repositories yet.</p>
          ) : null}
        </div>
      </section>
    </Loadable>
  );
}
