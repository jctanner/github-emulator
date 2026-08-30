import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type User = components["schemas"]["UserResponse"];
type Repository = components["schemas"]["RepoResponse"];

export function ProfilePage() {
  const {owner = ""} = useParams();
  const profile = useApiData<{user: User; repositories: Repository[]}>(
    `profile:${owner}`,
    async () => {
      const [userResult, reposResult] = await Promise.all([
        api.GET("/api/v3/users/{username}", {
          params: {path: {username: owner}},
        }),
        api.GET("/api/v3/users/{username}/repos", {
          params: {path: {username: owner}},
        }),
      ]);
      return {
        user: requireApiData(
          userResult.data,
          userResult.response,
          "Could not load profile.",
        ),
        repositories: requireApiData(
          reposResult.data,
          reposResult.response,
          "Could not load repositories.",
        ),
      };
    },
  );

  return (
    <Loadable loading={profile.loading} error={profile.error}>
      {profile.data ? (
        <div className="profile-grid">
          <aside>
            <div className="avatar-placeholder" aria-hidden="true">
              {profile.data.user.login.slice(0, 1).toUpperCase()}
            </div>
            <h1>{profile.data.user.name ?? profile.data.user.login}</h1>
            <p className="muted">@{profile.data.user.login}</p>
            {profile.data.user.bio ? <p>{profile.data.user.bio}</p> : null}
          </aside>
          <section>
            <h2>Repositories</h2>
            <div className="list-box">
              {profile.data.repositories.map((repository) => (
                <article className="list-row" key={repository.id}>
                  <h3>
                    <Link to={`/${repository.full_name}`}>
                      {repository.name}
                    </Link>
                  </h3>
                  <p>{repository.description}</p>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </Loadable>
  );
}
