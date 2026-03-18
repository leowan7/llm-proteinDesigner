import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { AuthLayout } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";

const resetPasswordConfirmSchema = z
  .object({
    password: z.string().min(8, "Password must be at least 8 characters."),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type ResetPasswordConfirmFormValues = z.infer<typeof resetPasswordConfirmSchema>;

type ExchangeState = "loading" | "ready" | "error";

export function ResetPasswordConfirm() {
  const navigate = useNavigate();
  const [exchangeState, setExchangeState] = useState<ExchangeState>("loading");
  const [apiError, setApiError] = useState<string | null>(null);

  const form = useForm<ResetPasswordConfirmFormValues>({
    resolver: zodResolver(resetPasswordConfirmSchema),
    defaultValues: {
      password: "",
      confirmPassword: "",
    },
  });

  const { isSubmitting } = form.formState;

  // On mount: parse the URL hash fragment and exchange the recovery tokens for
  // an HTTP-only cookie before showing the password form.
  useEffect(() => {
    async function exchangeToken() {
      const hash = window.location.hash.substring(1); // strip leading '#'
      const params = new URLSearchParams(hash);
      const accessToken = params.get("access_token");
      const refreshToken = params.get("refresh_token");

      if (!accessToken || !refreshToken) {
        setExchangeState("error");
        return;
      }

      try {
        await api("/auth/exchange-token", {
          method: "POST",
          body: { access_token: accessToken, refresh_token: refreshToken },
        });
        // Clear hash from URL so tokens are not visible in browser history
        window.history.replaceState(null, "", window.location.pathname);
        setExchangeState("ready");
      } catch {
        setExchangeState("error");
      }
    }

    void exchangeToken();
  }, []);

  async function onSubmit(values: ResetPasswordConfirmFormValues) {
    setApiError(null);
    try {
      await api("/auth/update-password", {
        method: "POST",
        body: { password: values.password },
      });
      navigate("/login");
    } catch (error) {
      if (error instanceof ApiError) {
        setApiError("Unable to connect. Check your connection and try again.");
      } else {
        setApiError("Unable to connect. Check your connection and try again.");
      }
    }
  }

  const footer = (
    <Link to="/login" className="hover:text-foreground hover:underline">
      Back to sign in
    </Link>
  );

  if (exchangeState === "loading") {
    return (
      <AuthLayout
        title="Set a new password"
        subtitle="Choose a strong password for your account."
        footer={footer}
      >
        <div className="flex items-center justify-center py-4">
          <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
        </div>
      </AuthLayout>
    );
  }

  if (exchangeState === "error") {
    return (
      <AuthLayout
        title="Set a new password"
        subtitle="Choose a strong password for your account."
        footer={footer}
      >
        <div className="space-y-4">
          <p className="text-sm font-medium text-destructive">
            Invalid or expired reset link. Please request a new one.
          </p>
          <Link
            to="/reset-password"
            className="text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            Request a new reset link
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Set a new password"
      subtitle="Choose a strong password for your account."
      footer={footer}
    >
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>New password</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    placeholder="At least 8 characters"
                    className="placeholder:text-muted-foreground/40"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="confirmPassword"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Confirm password</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    placeholder="Re-enter your password"
                    className="placeholder:text-muted-foreground/40"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          {apiError && (
            <p className="text-sm font-medium text-destructive">{apiError}</p>
          )}

          <Button
            type="submit"
            className="w-full min-h-[44px]"
            disabled={isSubmitting}
            aria-disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
                Updating...
              </>
            ) : (
              "Set new password"
            )}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
