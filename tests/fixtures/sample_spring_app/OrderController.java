package com.example.orders;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {

    @Autowired
    private OrderRepository orderRepository;

    @Autowired
    private PaymentClient paymentClient;

    @Autowired
    private OrderEventsPublisher eventsPublisher;

    @PostMapping("/{id}/submit")
    public ResponseEntity<OrderResponse> createOrder(
            @PathVariable String id, @RequestBody OrderRequest request) {
        Order order = orderRepository.findById(id);
        if (order == null) {
            return ResponseEntity.notFound().build();
        }
        boolean charged = paymentClient.charge(order.getTotal());
        if (!charged) {
            return ResponseEntity.status(402).build();
        }
        orderRepository.save(order.markSubmitted());
        eventsPublisher.publishSubmitted(order.getId());
        return ResponseEntity.ok(new OrderResponse(order));
    }

    @GetMapping("/{id}")
    public ResponseEntity<OrderResponse> getOrder(@PathVariable String id) {
        Order order = orderRepository.findById(id);
        if (order == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(new OrderResponse(order));
    }
}
