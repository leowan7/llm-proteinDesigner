import { useEffect } from "react";
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from "react-router-dom";
import { SignUp } from "./pages/SignUp";
import { Login } from "./pages/Login";
import { VerifyEmail } from "./pages/VerifyEmail";
import { EmailConfirmed } from "./pages/EmailConfirmed";
import { ResetPassword } from "./pages/ResetPassword";
import { ResetPasswordConfirm } from "./pages/ResetPasswordConfirm";

/**
 * Detects Supabase auth redirects with tokens in the URL hash fragment
 * and routes to the appropriate page.
 *
 * After email confirmation: #access_token=...&type=signup → /email-confirmed
 * After password reset:    #access_token=...&type=recovery → /reset-password/confirm (keeps hash)
 * On error:                #error=access_denied → /login
 */
function HashRedirectHandler() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const hash = window.location.hash.substring(1);
    if (!hash) return;

    const params = new URLSearchParams(hash);
    const type = params.get("type");
    const error = params.get("error");

    if (error) {
      navigate("/login", { replace: true });
      return;
    }

    if (type === "signup") {
      window.history.replaceState(null, "", "/email-confirmed");
      navigate("/email-confirmed", { replace: true });
    } else if (type === "recovery") {
      navigate(`/reset-password/confirm${window.location.hash}`, { replace: true });
    }
  }, [location, navigate]);

  return null;
}

function App() {
  return (
    <BrowserRouter>
      <HashRedirectHandler />
      <div className="dark min-h-screen bg-background font-sans text-foreground antialiased">
        <Routes>
          <Route path="/signup" element={<SignUp />} />
          <Route path="/login" element={<Login />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/email-confirmed" element={<EmailConfirmed />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/reset-password/confirm" element={<ResetPasswordConfirm />} />
          <Route
            path="/"
            element={
              <div className="flex items-center justify-center min-h-screen">
                <p className="text-muted-foreground">LLM Protein Designer -- Logged in</p>
              </div>
            }
          />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;
