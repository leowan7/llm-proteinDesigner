import { BrowserRouter, Routes, Route } from "react-router-dom";
import { SignUp } from "./pages/SignUp";
import { Login } from "./pages/Login";
import { VerifyEmail } from "./pages/VerifyEmail";
import { EmailConfirmed } from "./pages/EmailConfirmed";
import { ResetPassword } from "./pages/ResetPassword";
import { ResetPasswordConfirm } from "./pages/ResetPasswordConfirm";

function App() {
  return (
    <BrowserRouter>
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
