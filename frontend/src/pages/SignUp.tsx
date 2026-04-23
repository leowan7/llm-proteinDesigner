import { useState } from "react";
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
import { TOS_VERSION } from "@/lib/legal";

const signUpSchema = z
  .object({
    email: z.string().email("Enter a valid email address."),
    password: z.string().min(8, "Password must be at least 8 characters."),
    confirmPassword: z.string(),
    // Plan 10-02: must literally be true — unchecked box fails validation.
    // Zod 4 takes the message directly; mismatch renders the string below.
    tosAccepted: z.literal(true, {
      message: "You must accept the Terms of Service and Privacy Policy.",
    }),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type SignUpFormValues = z.infer<typeof signUpSchema>;

export function SignUp() {
  const navigate = useNavigate();
  const [apiError, setApiError] = useState<string | null>(null);

  const form = useForm<SignUpFormValues>({
    resolver: zodResolver(signUpSchema),
    defaultValues: {
      email: "",
      password: "",
      confirmPassword: "",
      // zod literal(true) still requires the field to be defined; default
      // to `false as const` so the box starts unchecked and the user must
      // interact before the schema passes.
      tosAccepted: false as unknown as true,
    },
  });

  const { isSubmitting } = form.formState;

  async function onSubmit(values: SignUpFormValues) {
    setApiError(null);
    try {
      await api("/auth/signup", {
        method: "POST",
        body: {
          email: values.email,
          password: values.password,
          tos_version: TOS_VERSION,
        },
      });
      navigate(`/verify-email?email=${encodeURIComponent(values.email)}`);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 409) {
          setApiError(
            "An account with this email already exists. Sign in instead."
          );
        } else if (error.status === 400 && /terms of service/i.test(error.detail)) {
          setApiError(
            "The Terms of Service have been updated. Refresh and try again."
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
    <span>
      Already have an account?{" "}
      <Link
        to="/login"
        className="hover:text-foreground hover:underline"
      >
        Sign in
      </Link>
    </span>
  );

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Enter your email and a password to get started."
      footer={footer}
    >
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

          <FormField
            control={form.control}
            name="tosAccepted"
            render={({ field }) => (
              <FormItem className="flex flex-row items-start gap-2 space-y-0">
                <FormControl>
                  <input
                    id="tosAccepted"
                    type="checkbox"
                    checked={field.value === true}
                    onChange={(e) => field.onChange(e.target.checked)}
                    onBlur={field.onBlur}
                    aria-describedby="tos-text"
                    className="mt-1 size-4 rounded border-input"
                  />
                </FormControl>
                <div className="leading-tight">
                  <FormLabel
                    htmlFor="tosAccepted"
                    id="tos-text"
                    className="text-sm font-normal"
                  >
                    I agree to the{" "}
                    <Link
                      to="/legal/terms"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline hover:text-foreground"
                    >
                      Terms of Service
                    </Link>{" "}
                    and{" "}
                    <Link
                      to="/legal/privacy"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline hover:text-foreground"
                    >
                      Privacy Policy
                    </Link>
                    .
                  </FormLabel>
                  <FormMessage />
                </div>
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
                Creating account...
              </>
            ) : (
              "Create account"
            )}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
