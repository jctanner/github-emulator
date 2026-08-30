import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type User = components["schemas"]["UserResponse"];
type Organization = components["schemas"]["OrganizationResponse"];
type Repository = components["schemas"]["RepoResponse"];

interface ProfileData {
  login: string;
  name?: string | null;
  summary?: string | null;
  kind: "user" | "organization";
  repositories: Repository[];
}

export function ProfilePage() {
  const {owner = ""} = useParams();
  const profile = useApiData<ProfileData>(`profile:${owner}`, async () => {
    const userResult = await api.GET("/api/v3/users/{username}", {
      params: {path: {username: owner}},
    });
    if (userResult.data) {
      const reposResult = await api.GET("/api/v3/users/{username}/repos", {
        params: {path: {username: owner}},
      });
      const user: User = userResult.data;
      return {
        login: user.login,
        name: user.name,
        summary: user.bio,
        kind: "user",
        repositories: requireApiData(
          reposResult.data,
          reposResult.response,
          "Could not load repositories.",
        ),
      };
    }
    if (userResult.response.status !== 404) {
      throw new Error("Could not load profile.");
    }

    const [orgResult, reposResult] = await Promise.all([
      api.GET("/api/v3/orgs/{org}", {params: {path: {org: owner}}}),
      api.GET("/api/v3/orgs/{org}/repos", {
        params: {path: {org: owner}},
      }),
    ]);
    const organization: Organization = requireApiData(
      orgResult.data,
      orgResult.response,
      "Could not load organization.",
    );
    return {
      login: organization.login,
      name: organization.name,
      summary: organization.description,
      kind: "organization",
      repositories: requireApiData(
        reposResult.data,
        reposResult.response,
        "Could not load repositories.",
      ),
    };
  });

  return (
    <Loadable loading={profile.loading} error={profile.error}>
      {profile.data ? (
        <div className="profile-grid">
          <aside>
            <div className="avatar-placeholder" aria-hidden="true">
              {profile.data.login.slice(0, 1).toUpperCase()}
            </div>
            <h1>{profile.data.name ?? profile.data.login}</h1>
            <p className="muted">
              @{profile.data.login} · {profile.data.kind}
            </p>
            {profile.data.summary ? <p>{profile.data.summary}</p> : null}
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
