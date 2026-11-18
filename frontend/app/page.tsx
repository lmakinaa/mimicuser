import { redirect } from "next/navigation";

/**
 * A single run is just a study with one customer, so there is no second place to
 * start one. Everything enters through /studies.
 */
export default function Home() {
  redirect("/studies");
}
