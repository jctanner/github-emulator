import {BrowserRouter, Route, Routes} from "react-router-dom";

import {SessionProvider} from "./auth/SessionContext";
import {AppShell} from "./components/AppShell";
import {DashboardPage} from "./pages/DashboardPage";
import {BranchesPage} from "./pages/BranchesPage";
import {ActionJobPage} from "./pages/ActionJobPage";
import {ActionRunPage} from "./pages/ActionRunPage";
import {ActionsPage} from "./pages/ActionsPage";
import {AdminPage} from "./pages/AdminPage";
import {CodeBrowserPage} from "./pages/CodeBrowserPage";
import {CommitPage} from "./pages/CommitPage";
import {CommitsPage} from "./pages/CommitsPage";
import {IssueDetailPage} from "./pages/IssueDetailPage";
import {IssuesPage} from "./pages/IssuesPage";
import {FileEditorPage} from "./pages/FileEditorPage";
import {LabelsPage} from "./pages/LabelsPage";
import {LoginPage} from "./pages/LoginPage";
import {NewIssuePage} from "./pages/NewIssuePage";
import {NewPullPage} from "./pages/NewPullPage";
import {NewRepositoryPage} from "./pages/NewRepositoryPage";
import {MigrationPage} from "./pages/MigrationPage";
import {ProfilePage} from "./pages/ProfilePage";
import {PullDetailPage} from "./pages/PullDetailPage";
import {PullsPage} from "./pages/PullsPage";
import {RepositoryPage} from "./pages/RepositoryPage";
import {RunnersPage} from "./pages/RunnersPage";
import {SearchPage} from "./pages/SearchPage";
import {SettingsPage} from "./pages/SettingsPage";
import {TagsPage} from "./pages/TagsPage";

export function App() {
  return (
    <BrowserRouter basename="/ui">
      <SessionProvider>
        <AppShell>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/new" element={<NewRepositoryPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/_admin/*" element={<AdminPage />} />
            <Route path="/:owner/:repo/issues" element={<IssuesPage />} />
            <Route path="/:owner/:repo/labels" element={<LabelsPage />} />
            <Route path="/:owner/:repo/issues/new" element={<NewIssuePage />} />
            <Route
              path="/:owner/:repo/issues/:number"
              element={<IssueDetailPage />}
            />
            <Route path="/:owner/:repo/pulls" element={<PullsPage />} />
            <Route path="/:owner/:repo/pulls/new" element={<NewPullPage />} />
            <Route
              path="/:owner/:repo/pulls/:number"
              element={<PullDetailPage />}
            />
            <Route path="/:owner/:repo/actions" element={<ActionsPage />} />
            <Route
              path="/:owner/:repo/actions/runs/:runId"
              element={<ActionRunPage />}
            />
            <Route
              path="/:owner/:repo/actions/jobs/:jobId"
              element={<ActionJobPage />}
            />
            <Route
              path="/:owner/:repo/actions/runners"
              element={<RunnersPage />}
            />
            <Route path="/:owner/:repo/settings/*" element={<SettingsPage />} />
            <Route
              path="/:owner/:repo/tree/:ref/*"
              element={<CodeBrowserPage />}
            />
            <Route
              path="/:owner/:repo/blob/:ref/*"
              element={<CodeBrowserPage blob />}
            />
            <Route
              path="/:owner/:repo/new/:ref/*"
              element={<FileEditorPage create />}
            />
            <Route
              path="/:owner/:repo/edit/:ref/*"
              element={<FileEditorPage />}
            />
            <Route
              path="/:owner/:repo/commits/:ref"
              element={<CommitsPage />}
            />
            <Route path="/:owner/:repo/commit/:sha" element={<CommitPage />} />
            <Route path="/:owner/:repo/branches" element={<BranchesPage />} />
            <Route path="/:owner/:repo/tags" element={<TagsPage />} />
            <Route path="/:owner/:repo" element={<RepositoryPage />} />
            <Route path="/:owner" element={<ProfilePage />} />
            <Route path="*" element={<MigrationPage />} />
          </Routes>
        </AppShell>
      </SessionProvider>
    </BrowserRouter>
  );
}
