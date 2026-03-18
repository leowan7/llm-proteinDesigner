import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AuthLayout } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export function VerifyEmail() {
  const [searchParams] = useSearchParams();
  const email = searchParams.get("email") ?? "";
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  async function handleResend() {
    try {
      await api("/auth/signup", {
        method: "POST",
        body: { email, password: "" },
      });
      setResendMessage("Verification email resent");
      setTimeout(() => setResendMessage(null), 3000);
    } catch {
      // Silently ignore errors on resend — user can try again
    }
  }

  const footer = (
    <Link to="/login" className="hover:text-foreground hover:underline">
      Back to sign in
    </Link>
  );

  return (
    <AuthLayout
      title="Check your inbox"
      subtitle={`We sent a verification link to ${email}. Click it to activate your account.`}
      footer={footer}
    >
      <div className="space-y-4">
        <Button
          type="button"
          variant="ghost"
          className="w-full min-h-[44px]"
          onClick={handleResend}
        >
          Resend verification email
        </Button>

        {resendMessage && (
          <p className="text-center text-sm text-muted-foreground">
            {resendMessage}
          </p>
        )}
      </div>
    </AuthLayout>
  );
}
