import { useNavigate } from "react-router-dom";

import { AuthLayout } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";

export function EmailConfirmed() {
  const navigate = useNavigate();

  return (
    <AuthLayout
      title="Email verified"
      subtitle="Your account is active. You can now sign in."
    >
      <Button
        type="button"
        className="w-full min-h-[44px]"
        onClick={() => navigate("/login")}
      >
        Sign in
      </Button>
    </AuthLayout>
  );
}
