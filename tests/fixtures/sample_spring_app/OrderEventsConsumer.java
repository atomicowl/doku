package com.example.orders;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class OrderEventsConsumer {

    @Autowired
    private OrderRepository orderRepository;

    @Autowired
    private ShippingClient shippingClient;

    @KafkaListener(topics = "order-events", groupId = "orders-svc")
    public void onMessage(String payload) {
        Order order = orderRepository.findByPayload(payload);
        if (order.isPaid()) {
            shippingClient.scheduleShipment(order.getId());
        }
    }
}
