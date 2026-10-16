/** Primary student mailbox format eligible for invitation-free registration. */
export function isCampusStudentEmail(value: string) {
  return /^[0-9]{13}@std\.uestc\.edu\.cn$/.test(value.trim().toLowerCase());
}
