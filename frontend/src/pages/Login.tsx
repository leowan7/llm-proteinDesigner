import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { AuthLayout } from "@/components/auth/AuthLayout";
import { setSentryUser } from "@/lib/sentry";
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

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Password is required."),
});

type LoginFormValues = z.infer<typeof loginSchema>;

export function Login() {
  const navigate = useNavigate();
  const [apiError, setApiError] = useState<string | null>(null);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  });

  const { isSubmitting } = form.formState;

  async function onSubmit(values: LoginFormValues) {
    setApiError(null);
    setUnverifiedEmail(null);
    try {
      const result = await api<{ user_id: string }>("/auth/login", {
        method: "POST",
        body: { email: values.email, password: values.password },
      });
      setSentryUser(result.user_id, values.email);
      navigate("/chat");
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 401) {
          setApiError("Incorrect email or password.");
        } else if (error.status === 403) {
          setUnverifiedEmail(values.email);
          setApiError(
            "Verify your email before signing in. Check your inbox or resend the link below."
          );
        } else {
          setApiError("Unable to connect. Check your connection and try again.");
        }
      } else {
        setApiError("Unable to connect. Check your connection and try again.");
      }
    }
  }

  const footer = (
    <>
      <span>
        Don&apos;t have an account?{" "}
        <Link to="/signup" className="hover:text-foreground hover:underline">
          Create one
        </Link>
      </span>
      <span>
        Forgot your password?{" "}
        <Link
          to="/reset-password"
          className="hover:text-foreground hover:underline"
        >
          Reset it
        </Link>
      </span>
    </>
  );

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back." footer={footer}>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    placeholder="you@example.com"
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
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Password</FormLabel>
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

          {apiError && (
            <div className="space-y-1">
              <p className="text-sm font-medium text-destructive">{apiError}</p>
              {unverifiedEmail && (
                <Link
                  to={`/verify-email?email=${encodeURIComponent(unverifiedEmail)}`}
                  className="text-sm text-muted-foreground hover:text-foreground hover:underline"
                >
                  Go to verification page
                </Link>
              )}
            </div>
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
                Signing in...
              </>
            ) : (
              "Sign in"
            )}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
