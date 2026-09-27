output "runs" {
  description = "Per run: the worker and support instance ids and the support host's private IP."
  value = {
    for k, w in aws_instance.worker : k => {
      worker_id  = w.id
      support_id = aws_instance.support[k].id
      support_ip = aws_instance.support[k].private_ip
    }
  }
}

output "results_bucket" {
  description = "The results bucket (its name carries the account id)."
  value       = local.results_bucket
  sensitive   = true
}
