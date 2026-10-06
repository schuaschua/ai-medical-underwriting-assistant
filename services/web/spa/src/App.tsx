import { useEffect, useRef } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Navigation } from "./components/Navigation";
import { RoleSwitcher } from "./components/RoleSwitcher";
import { homePath, SCREENS } from "./navigation";
import { useRole } from "./role/roleStore";
import { ChooseRole } from "./screens/ChooseRole";
import { NotFound } from "./screens/NotFound";
import { strings } from "./strings";

export function App() {
  const role = useRole();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const previousRole = useRef(role);

  // When the role changes (chosen here, switched here or in another tab) and
  // the screen being shown is not one of the new role's, go to its home.
  useEffect(() => {
    if (role === previousRole.current) {
      return;
    }
    previousRole.current = role;
    if (
      role !== null &&
      !SCREENS[role].some((screen) => screen.path === pathname)
    ) {
      void navigate(homePath(role), { replace: true });
    }
  }, [role, pathname, navigate]);

  return (
    <>
      <header>
        <h1>{strings.appTitle}</h1>
        <p>{strings.demoNotice}</p>
        {role !== null && <RoleSwitcher role={role} />}
        {role !== null && <Navigation role={role} />}
      </header>
      <hr />
      <main>
        {role === null ? (
          <ChooseRole />
        ) : (
          // Only the current role's screens are routes. This is a convenience:
          // the server checks the role on every call (security.md rule 20).
          // The boundary shows a plain message if a screen fails to render.
          <ErrorBoundary key={`${role}:${pathname}`}>
            <Routes>
              <Route
                path="/"
                element={<Navigate to={homePath(role)} replace />}
              />
              {SCREENS[role].map((screen) => (
                <Route
                  key={screen.path}
                  path={screen.path}
                  element={screen.element}
                />
              ))}
              <Route path="*" element={<NotFound role={role} />} />
            </Routes>
          </ErrorBoundary>
        )}
      </main>
    </>
  );
}
