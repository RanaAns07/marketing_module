from rest_framework import serializers


class LeadAcquisitionItemSerializer(serializers.Serializer):
    source_channel = serializers.CharField(required=False)
    campaign = serializers.CharField(required=False)
    source_form = serializers.CharField(required=False)
    count = serializers.IntegerField()


class LeadAcquisitionOverTimeSerializer(serializers.Serializer):
    period = serializers.CharField()
    count = serializers.IntegerField()


class LeadAcquisitionReportSerializer(serializers.Serializer):
    start_date = serializers.CharField()
    end_date = serializers.CharField()
    total_leads = serializers.IntegerField()
    by_source_channel = LeadAcquisitionItemSerializer(many=True)
    by_campaign = LeadAcquisitionItemSerializer(many=True)
    by_form = LeadAcquisitionItemSerializer(many=True)
    over_time = LeadAcquisitionOverTimeSerializer(many=True)
    note = serializers.CharField()


class PipelineStatusItemSerializer(serializers.Serializer):
    status_id = serializers.CharField()
    status_name = serializers.CharField()
    order = serializers.IntegerField()
    count = serializers.IntegerField()


class PipelineTagItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField()
    count = serializers.IntegerField()


class PipelineConversionCountsSerializer(serializers.Serializer):
    reached_appointment_booked = serializers.IntegerField()
    reached_customer = serializers.IntegerField()


class PipelineConversionRatesSerializer(serializers.Serializer):
    appointment_booked_rate = serializers.FloatField()
    customer_rate = serializers.FloatField()


class PipelineReportSerializer(serializers.Serializer):
    total_leads = serializers.IntegerField()
    by_status = PipelineStatusItemSerializer(many=True)
    by_tag = PipelineTagItemSerializer(many=True)
    conversion_counts = PipelineConversionCountsSerializer()
    conversion_rates = PipelineConversionRatesSerializer()


class TeamWorkloadMemberSerializer(serializers.Serializer):
    membership_id = serializers.CharField()
    user_id = serializers.CharField()
    name = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.CharField()
    is_available = serializers.BooleanField()
    assigned_leads_count = serializers.IntegerField()
    open_conversations_count = serializers.IntegerField()
    resolved_conversations_count = serializers.IntegerField()


class WorkflowPerformanceItemSerializer(serializers.Serializer):
    workflow_id = serializers.CharField()
    workflow_name = serializers.CharField()
    status = serializers.CharField()
    version = serializers.IntegerField()
    enrolled_count = serializers.IntegerField()
    active_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    paused_count = serializers.IntegerField()
    stopped_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    failed_step_counts = serializers.IntegerField()
    reply_counts = serializers.IntegerField()


class WorkflowPerformanceReportSerializer(serializers.Serializer):
    start_date = serializers.CharField()
    end_date = serializers.CharField()
    workflows = WorkflowPerformanceItemSerializer(many=True)


class MessagingDirectionCountSerializer(serializers.Serializer):
    channel = serializers.CharField()
    direction = serializers.CharField()
    count = serializers.IntegerField()


class MessagingReportSerializer(serializers.Serializer):
    start_date = serializers.CharField()
    end_date = serializers.CharField()
    by_channel_and_direction = MessagingDirectionCountSerializer(many=True)
    sent_count = serializers.IntegerField()
    delivered_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    bounce_count = serializers.IntegerField()
    opt_out_count = serializers.IntegerField()
    delivery_rate = serializers.FloatField()
    failure_rate = serializers.FloatField()
    cost_summary = serializers.DictField(required=False, allow_null=True)
    note = serializers.CharField()


class DrillDownDescriptorSerializer(serializers.Serializer):
    resource = serializers.CharField()
    params = serializers.DictField()


class DashboardMetricValueSerializer(serializers.Serializer):
    value = serializers.JSONField()
    drill_down = DrillDownDescriptorSerializer()


class EstimatedSpendMetricSerializer(serializers.Serializer):
    amount = serializers.FloatField()
    currency = serializers.CharField()
    is_estimate = serializers.BooleanField()
    disclaimer = serializers.CharField()
    drill_down = DrillDownDescriptorSerializer()


class DashboardResponseSerializer(serializers.Serializer):
    range = serializers.CharField()
    start_date = serializers.CharField()
    end_date = serializers.CharField()
    new_leads = DashboardMetricValueSerializer()
    open_conversations = DashboardMetricValueSerializer()
    waiting_conversations = DashboardMetricValueSerializer()
    unread_replies = DashboardMetricValueSerializer()
    leads_by_stage = DashboardMetricValueSerializer()
    active_workflows = DashboardMetricValueSerializer()
    messages_sent = DashboardMetricValueSerializer()
    delivery_rate = DashboardMetricValueSerializer()
    estimated_spend = EstimatedSpendMetricSerializer(required=False)
    integration_health = serializers.DictField(required=False)
