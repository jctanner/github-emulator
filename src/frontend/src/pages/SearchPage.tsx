import {FormEvent, useEffect, useState} from "react";
import {Link, useNavigate, useSearchParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Results = components["schemas"]["RepositorySearchResponse"];

export function SearchPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const query = params.get("q") ?? "";
  const [value, setValue] = useState(query);
  useEffect(() => setValue(query), [query]);
  const result = useApiData<Results>(`search:${query}`, async () => {
    if (!query) return {total_count: 0, incomplete_results: false, items: []};
    const {data, response} = await api.GET("/api/v3/search/repositories", {
      params: {query: {q: query}},
    });
    return requireApiData(data, response, "Search failed.");
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    void navigate(`/search?q=${encodeURIComponent(value)}`);
  }
  return (
    <section>
      <div className="page-heading">
        <h1>Search</h1>
      </div>
      <form className="search-form" onSubmit={submit}>
        <input
          aria-label="Search repositories"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <button className="button" type="submit">
          Search
        </button>
      </form>
      <Loadable loading={result.loading} error={result.error}>
        <p className="muted">
          {result.data?.total_count ?? 0} repository results
        </p>
        <div className="list-box">
          {result.data?.items.map((repo) => (
            <div className="list-row" key={repo.id}>
              <h2>
                <Link to={`/${repo.full_name}`}>{repo.full_name}</Link>
              </h2>
              <p>{repo.description}</p>
            </div>
          ))}
        </div>
      </Loadable>
    </section>
  );
}
