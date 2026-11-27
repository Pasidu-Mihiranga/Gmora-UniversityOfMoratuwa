/** Problem details from the API (API-4). Messages are shown as given; they never contain input. */
export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  trace_id?: string;
}

export class ApiError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title ?? "Request failed");
    this.problem = problem;
  }

  get notImplemented(): boolean {
    return this.problem.status === 501;
  }
}

/** Unwrap an openapi-fetch result or throw ApiError, for use inside query functions. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError((result.error as Problem | undefined) ?? { status: result.response.status });
  }
  return result.data as T;
}
